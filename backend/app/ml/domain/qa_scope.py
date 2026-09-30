"""예측 질의응답 — **어디까지 읽고, 답이 어떤 상태인가**의 판단 (입력 → 출력만).

```text
refuse_unknown_asks · plan_reads   가격 갈래가 읽을 조합과 날 (기준일 · 예측 범위)
report_days                        배치 · 성능 갈래가 읽을 지난 날
worst · grade · combined_status    갈래마다의 읽기 성적과 답 전체의 상태
all_usable                         답에 든 조합을 전부 써도 되나
```

★ **DB · 시계를 부르지 않는다.** 기준일이 없으면 읽는 것, 「오늘」을 정하는 것은 그래프
  노드(`service/qa_graph.py`)가 하고 결과를 인자로 넘긴다.

🟢 **자리 (2026-09-29 · 재구성 BL-017).** 전에는 `qa_graph.py` 의 `gate` 노드와 도우미
  (`_report_days` · `_worst` · `_grade` · `_combined_status` · `_all_usable`)였다. 판정 순서 ·
  문구 · 결과는 같다. 공개 이름으로 올린 것은 그래프와 답 조립(`readmodel/qa_answer.py`)이
  함께 부르기 때문이다.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.ml.domain.qa_question import OUT_OF_SCOPE_ITEM, OUT_OF_SCOPE_KIND, valid
from app.ml.schemas.qa import QA_ITEMS, QA_KINDS, QA_MAX_OFFSET
from app.ml.schemas.qa_state import QaState

#: 한 답에 담을 조합 상한. 3 x 3 x 19일 = 171행이면 사람이 못 읽는다.
MAX_ITEMS = 3
MAX_KINDS = 3

def refuse_unknown_asks(state: QaState) -> QaState | None:
    """고른 품목 · 가격 종류가 우리 것이 아니면 거절한다. 괜찮으면 `None`.

    ★ 기준일을 읽기 **전에** 본다 (`gate` 노드의 차례 그대로) — 소관 밖 질문에 창고를 읽지 않는다.
    """
    items = valid(state.get("items") or [state.get("item")], QA_ITEMS)
    kinds = valid(state.get("kinds") or [state.get("kind")], QA_KINDS)
    if not items:
        bad = state.get("item")
        return {"status": "OUT_OF_SCOPE", "message": OUT_OF_SCOPE_ITEM.format(item=bad)}
    if not kinds:
        return {"status": "OUT_OF_SCOPE", "message": OUT_OF_SCOPE_KIND}
    return None


def plan_reads(state: QaState, base_dt: date) -> QaState:
    """고른 값이 범위 안인가. **LLM 이 고른 값을 그대로 쿼리에 넣지 않는다.**

    `base_dt` 는 그래프가 정한 전달표의 기준일이다. 돌려주는 것은 읽을 조합(`asks`)과 날,
    범위 밖이던 날 · 기본값을 썼는지 — 또는 읽을 것이 없을 때의 거절 상태.
    """
    items = valid(state.get("items") or [state.get("item")], QA_ITEMS)
    kinds = valid(state.get("kinds") or [state.get("kind")], QA_KINDS)
    req = state["request"]

    #   ★ 우선순위 — 요청이 직접 준 날짜 > LLM 이 고른 날짜 > 기본값(내일)
    #   ★ **날짜를 안 말했으면 오늘을 보여준다** (2026-09-15 · 사용자 지시로 바꿈).
    #     전에는 말없이 «내일 하루» 였다. 값은 맞지만 왜 하루뿐인지 안 밝혀서,
    #     사람이 «원래 하루치만 있나 보다» 하고 넘어간다 — 조용한 축소다.
    #     기본값을 썼다는 것을 `used_default` 로 들고 가 답에 한 줄로 적는다.
    used_default = not (req.dates or state.get("asked")
                        or any(a.get("dates") for a in state.get("asks") or []))
    fallback = list(req.dates or state.get("asked") or [req.as_of or base_dt])
    last = base_dt + timedelta(days=QA_MAX_OFFSET)

    #   ★ **묶음이 있으면 그것만 본다.** 없으면 품목 x 가격을 곱한다.
    raw_asks = state.get("asks") or [
        {"item": item, "kind": kind} for item in items[:MAX_ITEMS] for kind in kinds[:MAX_KINDS]
    ]
    asks: list[dict[str, Any]] = []
    out_of_range: set[date] = set()
    for entry in raw_asks:
        if entry.get("item") not in QA_ITEMS or entry.get("kind") not in QA_KINDS:
            continue
        wanted = list(entry.get("dates") or fallback)
        asks.append({
            "item": entry["item"],
            "kind": entry["kind"],
            "targets": sorted({d for d in wanted if base_dt < d <= last}),
            "wants_today": any(d == base_dt for d in wanted),
        })
        out_of_range |= {d for d in wanted if d < base_dt or d > last}
    if not asks:
        return {"status": "OUT_OF_SCOPE", "message": OUT_OF_SCOPE_KIND}

    #   ★ **범위 안 날이 하나도 없으면 오늘 값을 주지 않는다** (2026-09-15 · 화면).
    #     「30일 뒤」만 물었는데 오늘 값이 나가면, 물은 것과 다른 날을 답한 것이다.
    #     범위 밖 안내만 준다.
    if out_of_range and not any(a["targets"] or a["wants_today"] for a in asks):
        today = req.as_of or base_dt
        days = " · ".join(
            f"{d} ({(d - today).days:+d}일)" for d in sorted(out_of_range)
        )
        return {
            "status": "OUT_OF_SCOPE",
            "base_dt": base_dt,
            "out_of_range": sorted(out_of_range),
            "items": [a["item"] for a in asks],
            "kinds": [a["kind"] for a in asks],
            "message": (
                f"{days} 은 예측 범위 밖입니다. "
                f"{today} 부터 {today + timedelta(days=QA_MAX_OFFSET)} 까지 답할 수 있습니다."
            ),
        }

    return {
        "base_dt": base_dt,
        "items": [a["item"] for a in asks],
        "kinds": [a["kind"] for a in asks],
        "item": asks[0]["item"],
        "kind": asks[0]["kind"],
        "asks": asks,
        #   옛 이름 — 첫 묶음 기준. 한 묶음짜리 검사·분기가 아직 쓴다.
        "wants_today": any(a["wants_today"] for a in asks),
        "targets": sorted({d for a in asks for d in a["targets"]}),
        "out_of_range": sorted(out_of_range),
        "used_default": used_default,
    }


#: 한 답에 담을 **지난 날** 상한. 열흘치를 다 펼치면 답이 배치 표로 덮인다.
MAX_REPORT_DAYS = 7


def report_days(asked: list[date], on: date) -> tuple[list[date], list[date], bool]:
    """배치·성능이 읽을 날. 돌려주는 것은 (읽을 날, 기준일 뒤인 날, 잘랐나).

    :param asked: 해석기가 고른 날(맨 위 `dates`). 비었으면 날짜를 안 말한 것이다.
    :param on: 물어본 날 — 화면 기준일, 없으면 서울 오늘 (그래프의 `_asked_on`).

    ★ **날짜를 안 말했을 때만 오늘로 돌아간다** (2026-09-16 · 사용자 결정으로 고침).
      전에는 «지난 날만 남기고, 남는 게 없으면 오늘» 이었다. 그 규칙은 「5일 뒤 배추
      경락가랑 배치 상태」처럼 **배치의 날짜를 안 말한** 질문을 위한 것이었는데,
      「내일 배치」처럼 **앞날을 콕 집은** 질문까지 오늘로 바꿔 버렸다 — 물은 것과
      다른 날을 조용히 내민 것이다. 실제로 화면에서 그렇게 나왔다.

    🔴 **기준일 뒤는 읽지 않고 그렇게 말한다** (`AHEAD_BATCH`). «아직 안 돌았다» 고
      적지 않는다 — 화면 기준일은 진짜 오늘보다 과거일 수 있어서, 기준일을 09-14 로
      두면 09-15 기록은 **DB 에 있는데도** 앞날이다. 그때 «아직 안 돌았다» 는 거짓이다.

    ★ 갈래별로 날짜가 안 나뉘는 문제는 **해석기가 갈라 준다.** 가격에 붙은 날은
      짝 물음(`asks`) 안에만 들어오고, 여기서 보는 `asked` 는 맨 위 `dates` 뿐이다
      (2026-09-16 · 실제 응답 2건으로 확인. 검사 `test_가격_날짜만_있는_배치는_오늘을_본다`).

    🔴 **자르되 잘랐다고 말한다** (`_trimmed_line`). 조용히 줄이면 열흘을 물은
      사람이 이레만 보고 «열흘이 다 이렇구나» 로 읽는다.
    """
    asked = sorted(set(asked))
    if not asked:
        return [on], [], False
    days = [d for d in asked if d <= on]
    ahead = [d for d in asked if d > on]
    if len(days) > MAX_REPORT_DAYS:
        return days[-MAX_REPORT_DAYS:], ahead, True
    return days, ahead, False


def worst(grades: list[str]) -> str:
    """여러 날의 읽기 결과를 하나로. **못 읽은 것이 있으면 그것이 이긴다.**

    🔴 `grade` 와 뜻이 다르다. 저쪽은 «답이 나갔나»(하나라도 ok 면 ok)이고,
      이쪽은 «무엇이 고장인가» 다 — 어댑터가 표 이름을 댈 때 쓴다.
    """
    if "error" in grades:
        return "error"
    return "ok" if "ok" in grades else "empty"


#: 가격 갈래가 답을 못 냈을 때, 그것이 «못 읽음» 인가 «없음» 인가.
#: 되묻기·소관 밖은 **답을 한 것**이다 — 실패로 세지 않는다.
FORECAST_GRADE = {
    "SOURCE_UNAVAILABLE": "error",
    "LLM_UNAVAILABLE": "error",
    "NO_DATA": "empty",
}


def grade(grades: list[str]) -> str:
    """갈래 하나의 성적. **하나라도 나갔으면 ok** — 나머지는 한 줄로 말한다."""
    if "ok" in grades:
        return "ok"
    return "error" if "error" in grades else "empty"


def combined_status(routes: list[str], forecast: str | None, grades: list[str]) -> str:
    """갈래 여럿의 상태를 하나로.

    ★ **가격만 물었으면 예전 값 그대로다.** 갈래를 늘렸다고 이미 나가던 답의
      상태가 바뀌면, 그 값으로 분기하는 마스터 쪽이 조용히 달라진다.
    """
    if routes == ["forecast"]:
        return forecast or "OK"
    if grades and all(g == "error" for g in grades):
        return "SOURCE_UNAVAILABLE"
    if "error" in grades:
        return "PARTIAL"
    if grades and all(g == "empty" for g in grades):
        return "NO_DATA"
    if forecast in {"PARTIAL", "NEED_CLARIFY", "OUT_OF_SCOPE"}:
        return "PARTIAL"
    return "OK"


def all_usable(blocks: list[dict[str, Any]]) -> bool | None:
    """**답에 든 조합을 전부 써도 되나.** 하나라도 막혔으면 `False` 다.

    🔴 **첫 조합만 보던 것을 고쳤다** (2026-09-16 · 사용자 결정). 「가격 알려줘」에
      아홉 조합이 나가는데, 그중 양파 중도매가는 막힌 조합이다. 그런데 이 칸은
      **첫 조합(배추 경락가)** 만 보고 `True` 를 내보냈다 — 답 문장에서 «쓰지 마세요»
      경고를 뺀 뒤로는 그 사실이 **어디에도 안 남았다.**

    ```text
    하나라도 False   -> False    (막힌 것이 섞여 있다)
    전부 True        -> True
    그 밖            -> None     (모른다 — 조합이 하나뿐일 때 예전과 같다)
    ```

    ★ **답 문장은 한 글자도 안 바뀐다.** 바뀌는 것은 이 칸 하나의 뜻이고,
      판단은 그 값을 받는 마스터가 한다.
    """
    values = [(b.get("usability") or {}).get("use_recommended") for b in blocks]
    if any(value is False for value in values):
        return False
    if values and all(value is True for value in values):
        return True
    return None
