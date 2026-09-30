"""예측 질의응답 — LangGraph 그래프 (노드 여섯 · 분기 하나 · 출구 하나).

```text
START → supervise → gate ─(읽을 날이 있다)→ fetch ─┐
                      └─(되묻기 · 범위 밖 · 값 없음)─┴→ batch → perf → compose → END
```

★ **갈래가 셋이다** (2026-09-16). 한 질문이 여럿을 물을 수 있다.

    forecast  가격이 얼마인가 · 얼마나 맞나 · 써도 되나
    batch     그날 배치가 어떻게 됐나 + 그날 AI 점검 보고서 (**그대로** 붙인다)
    perf      봉인 개봉 성능표 + 그날 재학습 후보 비교

🔴 **해석기를 하나 더 두지 않았다.** LLM 호출은 여전히 **한 번**이고, 그 한 번이
   `routes` 목록을 돌려준다. **갈래를 나눠 처리하는 것은 코드가 한다** — 노드마다
   따로 읽고 compose 가 부른 순서대로 이어 붙인다.

🔴 **한 갈래가 터져도 나머지는 나간다.** 배치를 못 읽었다고 성능 답까지 버리면,
   사람은 무엇이 고장인지 모른 채 빈 화면을 본다. 못 읽은 갈래만 한 줄로 말한다.

★ **LLM 이 고르고, 규칙이 확인한다.**

    supervise   질문을 해석해 «어느 도구 · 어떤 인자» 를 고른다        ← LLM 자리
    gate        고른 값이 범위 안인지 확인한다                          ← 규칙
    fetch       표를 읽는다 (여러 날짜를 한 번에)                       ← 규칙
    compose     마크다운을 만든다. **유일한 출구다**                    ← 틀 (+ 나중에 LLM)

★ **부르는 길이 둘이다.** `question` 만 주면 LLM 이 해석하고, `item·kind·dates` 를
  직접 주면 해석을 건너뛴다. 둘째 길은 LLM 없이도 값과 서식을 확인하려고 남긴다.

🔴 **LLM 이 고른 값을 그대로 쓰지 않는다.** `gate` 가 다시 검사한다. 키가 없거나
   호출이 실패하면 «해석하지 못했습니다» 로 답한다 — 지어내지 않는다.

★ **체크포인트를 쓰지 않는다.** 대화 상태를 들고 있지 않다 (`compile()` 맨몸).
  이어지는 질문(「5일 뒤엔?」)은 마스터가 직전 맥락을 같이 넘겨 주는 것으로 푼다.
  저장소 전체에 체크포인트를 쓰는 곳이 없다 — 매입·판매 그래프도 같다.

🟢 **자리 (2026-09-29 · 재구성 BL-017).** 전에는 `app/ml/qa_graph.py` 한 파일에 노드 · 판단 ·
  마크다운이 함께 있었다. 이 파일에는 그래프 · 노드와 **읽기 · 해석기 · ML 백엔드를 부르는
  순서**만 남았다.

```text
판단 (입력 → 출력)   domain/qa_question.py (무엇을 물었나) · domain/qa_scope.py (범위 · 상태)
읽기                 readmodel/qa_reads.py (창고마다 조회 연결 하나) → repository/qa.py (SQL)
답 조립              readmodel/qa_answer.py (마크다운 · QaMeta · 근거 재료)
해석기 · ML 백엔드   llm/qa.py · ml_backend.py
```
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.core.clock import today_in_seoul
from app.ml import config, ml_backend
from app.ml.domain import qa_question, qa_scope
from app.ml.llm import qa as qa_llm
from app.ml.readmodel import qa_answer, qa_reads
from app.ml.schemas.qa import QaAnswer, QaRequest
from app.ml.schemas.qa_state import QaState


# ───────────────────────────────────────────────────────────── 노드
def supervise(state: QaState) -> QaState:
    """질문 → 어느 도구·어떤 인자. **고르기만 한다.**

    ★ 판단은 `domain/qa_question.py` 가 한다. 이 노드는 기준일을 읽고 해석기를 부르는 **순서**만
      갖는다 — 값으로 직접 준 길 · 질문 없는 길은 읽지도 부르지도 않는다(종전 그대로).
    """
    req = state["request"]
    chosen_without_question = qa_question.choose_without_question(req)
    if chosen_without_question is not None:
        return chosen_without_question

    #   ★ 기준일을 먼저 잡는다 — 「내일」이 며칠인지는 기준일이 있어야 정해진다.
    #
    #   🔴 **여기서 바로 포기하지 않는다** (2026-09-16). 예전에는 예측표를 못 읽으면
    #     그 자리에서 «못 읽었습니다» 로 끝냈다. 그런데 배치·성능은 **다른 표**를
    #     읽는다 — 예측표가 죽었다고 «오늘 배치 잘 됐어?» 에 답을 못 할 이유가 없다.
    #     무엇을 물었는지 안 뒤에 판단한다.
    source_down = False
    try:
        base_dt = qa_reads.latest_base_date(req.as_of)
    except Exception:                                    # noqa: BLE001
        base_dt, source_down = None, True

    #   ★ **「오늘」은 화면의 기준일이다** (2026-09-15 · 사용자 지시).
    #     화면(3000)은 날짜를 걸으며 채팅마다 그 날을 `as_of` 로 싣는다. 전에는
    #     «as_of 이하 최신 예측일» 을 오늘로 셌다 — 그날 예측이 없으면(휴일 등)
    #     하루 이틀 전 날이 「오늘」이 됐다. 해석기는 as_of 로 날을 센다.
    #   ★ 벽시계는 `core/clock.py` 하나로만 읽는다 — 서버가 UTC 면 하루 밀린다.
    today = req.as_of or base_dt or today_in_seoul()
    chosen = qa_llm.interpret(req.question, today)
    return qa_question.choose_from_interpretation(
        req, chosen, base_dt=base_dt, source_down=source_down
    )


def gate(state: QaState) -> QaState:
    """고른 값이 범위 안인가. **LLM 이 고른 값을 그대로 쿼리에 넣지 않는다.**

    ★ 범위 판단은 `domain/qa_scope.py` 가 한다. 이 노드는 기준일이 아직 없으면 읽는 **차례**만
      갖는다 — 소관 밖이면 읽기 전에 거절한다(종전 그대로).
    """
    if state.get("status"):
        return {}
    if "forecast" not in (state.get("routes") or ["forecast"]):
        #   가격을 안 물었으면 검사할 품목·날짜가 없다. 배치·성능은 여기를 안 지난다.
        return {}

    refused = qa_scope.refuse_unknown_asks(state)
    if refused is not None:
        return refused

    req = state["request"]
    base_dt = state.get("base_dt")
    if base_dt is None:
        try:
            base_dt = qa_reads.latest_base_date(req.as_of)
        except Exception:                                    # noqa: BLE001  DB 미연결·표 없음
            return {"status": "SOURCE_UNAVAILABLE", "message": qa_question.SOURCE_DOWN}
    if base_dt is None:
        return {"status": "NO_DATA", "message": qa_question.NO_FORECAST_YET}

    return qa_scope.plan_reads(state, base_dt)


def after_gate(state: QaState) -> str:
    """읽을 예측이 있으면 조회하고, 없으면 건너뛴다.

    ★ 건너뛰어도 **출구로 바로 가지 않는다** (2026-09-16). 배치·성능 노드가 뒤에
      있어서, 가격을 안 물었거나 되묻기로 빠진 질문도 그 갈래는 답해야 한다.
    """
    if state.get("status"):
        return "batch"
    return "fetch" if (state.get("targets") or state.get("wants_today")) else "batch"


def fetch(state: QaState) -> QaState:
    """표를 읽는다. **조합마다 한 블록** (품목 x 가격 종류).

    ★ 「배추 경락가랑 도매가」처럼 여럿을 물으면 조합이 여럿이다. 전에는 한 조합만
      읽어 **중도매가만 답하고 경락가를 조용히 버렸다** (2026-09-15 실측).

    🔴 조합 수를 막아 둔다. 3품목 x 3가격 x 19일 = 171행이면 화면이 덮인다.
    """
    base_dt = state["base_dt"]
    asks = state.get("asks") or [
        {"item": state["item"], "kind": state["kind"],
         "targets": list(state.get("targets") or []),
         "wants_today": bool(state.get("wants_today"))}
    ]
    try:
        blocks: list[dict[str, Any]] = []
        fell_back = False
        for ask in asks[:qa_scope.MAX_ITEMS * qa_scope.MAX_KINDS]:
            item, kind = ask["item"], ask["kind"]
            rows = qa_reads.forecast_rows(item, kind, base_dt, ask["targets"])
            today = (qa_reads.today_row(item, kind, base_dt)
                     if ask["wants_today"] else None)
            if state.get("used_default") and today is None and not rows:
                #   ★ 오늘 값이 없는 아침도 있다. **빈 답을 주지 말고 내일로 물러선다.**
                fell_back = True
                rows = qa_reads.forecast_rows(
                    item, kind, base_dt, [base_dt + timedelta(days=1)]
                )
            blocks.append({
                "item": item,
                "kind": kind,
                "targets": ask["targets"],
                "rows": rows,
                "today": today,
                "accuracy": qa_reads.accuracy(item, kind),
                "usability": qa_reads.usability(item, kind),
            })
        first = blocks[0] if blocks else {}
        return {
            "blocks": blocks,
            "default_fell_back": fell_back,
            #   옛 이름 — 첫 조합을 가리킨다. 한 조합짜리 검사·호출이 아직 쓴다.
            "rows": first.get("rows") or [],
            "today": first.get("today"),
            "accuracy": first.get("accuracy"),
            "usability": first.get("usability"),
        }
    except Exception:                                        # noqa: BLE001
        return {"status": "SOURCE_UNAVAILABLE", "message": qa_question.SOURCE_DOWN}


def _asked_on(state: QaState) -> date:
    """어느 날을 묻는가. **화면 기준일이고, 없으면 오늘이다.**

    ★ 예측 기준일(`base_dt`)을 쓰지 않는다. 배치·보고서는 **예측표와 다른 표**라
      예측이 없는 날에도 배치는 돌았을 수 있다.

    🔴 **`date.today()` 를 안 쓴다** (`core/clock.py`). 서버가 UTC 면 한국 09:00 이
      UTC 자정이라 **날짜가 하루 밀린다** — 09:00 배치를 물었는데 어제 것을 읽는다.
      CLAUDE.md §9 에 같은 사고가 적혀 있다.
    """
    return state["request"].as_of or today_in_seoul()


def batch_node(state: QaState) -> QaState:
    """날마다 배치 한 행 + 실패한 단계 + 그날 AI 점검 보고서. **셋 다 읽기만 한다.**

    🔴 **둘을 따로 감싼다.** 배치 행을 못 읽어도 보고서는 나갈 수 있고 그 반대도 된다.
      하나로 묶으면 한 번의 실패가 둘을 다 지운다.

    ★ **날짜가 여럿이면 날마다 읽는다** (2026-09-16). 「사흘치 배치」가 그렇다.
    """
    if "batch" not in (state.get("routes") or []):
        return {}
    #   ★ 기준일 뒤인 날은 **읽으러 가지도 않는다** — 없는 것이 아니라 안 보여주는 것이다.
    days, ahead, trimmed = qa_scope.report_days(state.get("asked") or [], _asked_on(state))
    blocks: list[dict[str, Any]] = []
    for on in days:
        block: dict[str, Any] = {"on": on, "row": None, "fails": [], "report": None}
        try:
            row = qa_reads.batch_run(on)
            block["row"] = row
            block["fails"] = qa_reads.failed_stages(row["run_id"]) if row else []
            block["read"] = "ok" if row else "empty"
        except Exception:                                    # noqa: BLE001
            block["read"] = "error"
        try:
            report = qa_reads.agent_report(config.CHECK_REPORT, on)
            block["report"] = report
            block["report_read"] = "ok" if report else "empty"
        except Exception:                                    # noqa: BLE001
            block["report_read"] = "error"
        blocks.append(block)

    #   ★ 앞날만 물었으면 **블록이 하나도 없다.** 그때도 옛 이름 칸은 채워 둔다 —
    #     읽은 것이 없다는 뜻으로 `empty` 다 (`qa_scope.worst` 와 같은 말).
    first = blocks[0] if blocks else {"on": ahead[0], "row": None,
                                      "fails": [], "report": None}
    return {
        "batch_days": days,
        "batch_ahead": ahead,
        "batch_blocks": blocks,
        "batch_trimmed": trimmed,
        #   옛 이름 — **첫 날**을 가리킨다. 근거(`qa_answer._batch_evidence`)와 한 날짜짜리
        #   검사가 아직 이 이름으로 읽는다.
        "batch_on": first["on"],
        "batch_row": first["row"],
        "batch_fails": first["fails"],
        "batch_read": qa_scope.worst([b["read"] for b in blocks]) if blocks else "empty",
        "check_row": first["report"],
        "check_read": qa_scope.worst([b["report_read"] for b in blocks]) if blocks else "empty",
    }


def perf_node(state: QaState) -> QaState:
    """봉인 개봉 성능표는 **상수**라 못 읽을 일이 없다. 읽는 것은 둘이다.

    ```text
    현재 모델      prediction_log + model_cutover
    재학습 보고서   그날 것 **전부** (판정 3건 · 검증 2건인 날이 있다)
    ```

    🔴 **따로 감싼다.** 현재 모델을 못 읽어도 재학습 이야기는 나갈 수 있고,
      그 반대도 된다. 하나로 묶으면 한 번의 실패가 둘을 다 지운다.
    """
    if "perf" not in (state.get("routes") or []):
        return {}
    #   ★ 재학습 보고서도 **물어본 날**을 읽는다 (2026-09-16 · 사용자 결정 ②).
    #     봉인 성능표와 현재 모델은 날짜와 무관하다 — 그건 늘 지금 것이다.
    days, ahead, trimmed = qa_scope.report_days(state.get("asked") or [], _asked_on(state))
    out: QaState = {"perf_days": days, "perf_ahead": ahead, "perf_trimmed": trimmed}

    try:
        found = qa_reads.current_models()
        out["models"] = list(found.get("models") or [])
        out["cutover_read"] = str(found.get("cutover_read") or "ok")
        out["models_read"] = "ok"
    except Exception:                                        # noqa: BLE001
        out["models"] = []
        out["models_read"] = "error"
        out["cutover_read"] = "error"

    rows: list[dict[str, Any]] = []
    read = "ok"
    try:
        for on in days:
            for name in config.RETRAIN_REPORTS:
                #   ★ **그날 것을 전부** 읽는다 (2026-09-16). 마지막 하나만 읽었더니
                #     경락가 검증의 «후보가 나쁩니다» 가 답에서 통째로 빠졌다.
                rows += qa_reads.agent_reports(name, on)
    except Exception:                                        # noqa: BLE001
        read = "error"
    out["retrain_rows"] = rows
    out["perf_read"] = read

    #   ★ **또 따로 감싼다.** ML 콘솔이 안 떠 있어도 위의 성능표와 보고서는
    #     그대로 나가야 한다. 못 읽으면 답에 그렇게 한 줄 적는다.
    try:
        out["pending"] = ml_backend.retrain_pending()
        out["pending_read"] = "ok"
    except Exception:                                        # noqa: BLE001
        out["pending"] = []
        out["pending_read"] = "error"
    return out


def compose(state: QaState) -> QaState:
    """유일한 출구 — 답 조립(`readmodel/qa_answer.py::compose_answer`)을 부른다.

    ★ 이 노드가 하는 일은 「물어본 날」을 정해 넘기는 것이다. 벽시계는 `core/clock.py` 하나로만
      읽고, 그 자리를 이 그래프 파일에 둔다 (`tests/core/test_clock_is_the_only_wall_clock.py`).
    """
    return qa_answer.compose_answer(state, _asked_on(state))


def build_graph():
    """노드 여섯 · 분기 하나. **체크포인트 없이** 맨몸으로 컴파일한다.

    ★ 배치·성능 노드는 **줄 세워 둔다.** 자기 갈래가 아니면 아무것도 안 하고 지나간다
      (`return {}`). 갈래마다 가지를 치면 조합이 여덟 갈래가 되는데, 그렇게 얻는 것이
      «안 물어본 노드를 안 지나간다» 뿐이라 값을 못 한다.
    """
    graph = StateGraph(QaState)
    graph.add_node("supervise", supervise)
    graph.add_node("gate", gate)
    graph.add_node("fetch", fetch)
    graph.add_node("batch", batch_node)
    graph.add_node("perf", perf_node)
    graph.add_node("compose", compose)

    graph.add_edge(START, "supervise")
    graph.add_edge("supervise", "gate")
    graph.add_conditional_edges("gate", after_gate, ["fetch", "batch"])
    graph.add_edge("fetch", "batch")
    graph.add_edge("batch", "perf")
    graph.add_edge("perf", "compose")
    graph.add_edge("compose", END)
    return graph.compile()


def answer(request: QaRequest) -> QaAnswer:
    """바깥에 드러내는 것은 이 함수 하나다. 그래프는 안쪽 사정이다.

    ★ **읽은 행을 같이 돌려준다** (2026-09-15). 마스터 어댑터가 회신에 붙일
      `Evidence` 를 그 행에서 만든다 — 답 문장에서 숫자를 다시 뜯어내면
      **같은 사실에 두 경로가 생긴다.** 값은 표에서 온 것 하나여야 한다.
    """
    final = build_graph().invoke({"request": request})
    return qa_answer.answer_from(final)
