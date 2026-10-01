"""예측 질의응답 — **답 조립.** 그래프가 읽어 온 것으로 마크다운과 기계용 값을 만든다.

```text
compose_answer(state, asked_on)   답 마크다운 + `QaMeta` + 상태 (그래프의 compose 노드가 부른다)
answer_from(final)                마친 상태 → `QaAnswer` (근거 재료 · 읽은 표 성적 · 현재 모델 포함)
```

★ **읽지 않는다.** DB · ML 백엔드 · 시계를 부르지 않고, 그래프 상태에 담긴 것만 글과 값으로
  옮긴다. 「물어본 날」(`asked_on`)도 그래프가 정해 넘긴다.

🟢 **자리 (2026-09-29 · 재구성 BL-017).** 전에는 `qa_graph.py` 의 `_*_section` · `_answer_markdown`
  · `compose` 본문 · `answer()` 뒤쪽(근거 재료)이었다. 설계서가 계획한 `readmodel/qa_markdown.py`
  대신 이 이름으로 둔 것은 마크다운과 함께 어댑터가 쓰는 기계용 재료도 같은 최종 상태에서
  만들기 때문이다. 문구 · 표 모양 · 값은 그대로다.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.ml import config
from app.ml.config import KIND_LABEL, KST
from app.ml.domain import qa_scope
from app.ml.schemas.forecast import SPEC
from app.ml.schemas.qa import QA_MAX_OFFSET, QaAnswer, QaMeta
from app.ml.schemas.qa_state import QaState


def _kst(value) -> str:
    """예측 시각을 한국 시간으로. 시간대를 모르는 값이면 그대로 적는다."""
    try:
        return value.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
    except (AttributeError, ValueError, TypeError):
        return str(value)


def _hhmm(value) -> str:
    """시각을 «09:00» 으로. **배치 기록은 UTC 라 한국 시간으로 돌린다.**

    🔴 한국 09:00 이 UTC 자정이다 (CLAUDE.md §9). 안 돌리면 09:00 배치가
      «00:00» 으로 보이고, 사람이 «한밤중에 돌았나» 로 읽는다.
    """
    if value is None:
        return "—"
    try:
        return value.astimezone(KST).strftime("%H:%M")
    except (AttributeError, ValueError, TypeError):
        return str(value)


def _stamp(value) -> str:
    """보고서 시각. **이미 한국 시간이라 돌리지 않는다** (`agent_report.ran_at`)."""
    try:
        return value.strftime("%Y-%m-%d %H:%M")
    except (AttributeError, ValueError, TypeError):
        return str(value)


def _cell(value) -> str:
    """표 한 칸. 줄바꿈과 막대기는 표를 깨뜨리므로 눕힌다. **글자는 안 지운다.**"""
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " · ").strip()

#: 날짜를 안 물어봐 하루만 답했을 때, **더 볼 수 있다고 알려주는 뒷문장.**
#:
#: 🔴 **「전부」라고 권하지 않는다** (2026-09-16 · 사용자 지시). 그 말은 우리
#:   해석기만 아는 말이고(`qa_llm.py` 의 지시문이 「전부」를 19일로 편다),
#:   **마스터를 거쳐 오면 못 알아듣는다.** 시키는 대로 «전부» 라고 말한 사람이
#:   답을 못 받는 안내였다.
#:
#: ★ 그래서 **되는 말을 예로 보인다.** 「일주일치」·「3일 뒤」는 날짜를 고르는
#:   보통 말이라 어느 길로 들어와도 읽힌다.
ASK_DATE_HINT = (
    "원하는 날짜나 기간을 말씀해주시면 해당 기간에 대한 가격을 보여드립니다. "
    "예) 일주일치의 가격 / 3일 뒤 가격"
)


#: 기록이 없는 날의 문구. **«고장» 이 아니라 «그날은 없다» 다** (2026-09-16 · 결정 ①).
#:
#: 🔴 이 문장이 화면에 그대로 나가야 한다. 어댑터가 이것을 `RUNTIME_NOT_READY` 로
#:   올리면 마스터가 답을 버리고 «창고를 쓸 수 없다» 를 대신 띄운다 — 실제로 그랬다.
NO_BATCH_ROW = "기준일({on})에 대한 배치 기록이 없습니다."
NO_CHECK_REPORT = "기준일({on})에 대한 점검 보고서{josa} 아직 없습니다."
NO_RETRAIN_ROW = "기준일({on})에 대한 재학습 판정 기록이 없습니다."

#: 기준일보다 **뒤인 날**을 물었을 때. 오늘 것을 대신 보여주지 않는다 (2026-09-16).
#:
#: 🔴 «아직 돌지 않았습니다» 라고 쓰지 않는다. 화면 기준일은 진짜 오늘보다 과거일 수
#:   있어서(시연 중 09-14 로 두면 09-15 기록은 **DB 에 있는데도** 앞날이다), 그때
#:   «아직 안 돌았다» 는 거짓이 된다. 맞는 말은 «기준일 뒤는 안 보여준다» 다.
AHEAD_BATCH = "배치 관련 정보는 기준일 또는 기준일보다 과거의 데이터만 조회 가능합니다."
AHEAD_PERF = "재학습 관련 정보는 기준일 또는 기준일보다 과거의 데이터만 조회 가능합니다."


def _trimmed_line(days: list[date]) -> str:
    """이레를 넘겨 잘랐을 때 붙이는 한 줄. **자른 사실을 숨기지 않는다.**"""
    return (
        f"> 물어보신 날이 많아 **최근 7일**({days[0]} ~ {days[-1]})만 보여드립니다."
    )


def _batch_section(state: QaState, asked_on: date) -> tuple[str, str]:
    """배치 갈래의 글 한 덩어리. 돌려주는 것은 (글, 성적).

    ★ **날짜마다 블록 하나다** (2026-09-16). 「사흘치 배치」면 블록이 셋이다.

    ★ 기준일 뒤인 날은 블록을 안 만들고 **맨 끝에 한 줄**로 밝힌다 (`AHEAD_BATCH`).
      「어제랑 내일 배치」면 어제는 표로 답하고 내일은 그 한 줄이다.

    🔴 빈 목록(`[]`)과 없는 칸을 가른다. 앞날만 물으면 블록이 **정말 하나도 없고**,
      그때 옛 길로 떨어지면 안 물어본 **오늘 표**가 나간다.
    """
    blocks = state.get("batch_blocks")
    if blocks is None:
        #   옛 길 — 한 날짜짜리 상태를 그대로 받은 자리(검사·직접 호출)가 아직 있다.
        blocks = [{
            "on": state.get("batch_on") or asked_on,
            "row": state.get("batch_row"),
            "fails": state.get("batch_fails") or [],
            "read": state.get("batch_read") or "empty",
            "report": state.get("check_row"),
            "report_read": state.get("check_read") or "empty",
        }]
    lines: list[str] = []
    grades: list[str] = []
    if state.get("batch_trimmed"):
        lines += [_trimmed_line([b["on"] for b in blocks]), ""]
    for index, block in enumerate(blocks):
        if index:
            lines.append("")
        body, grade = _batch_block(block)
        lines += body
        grades.append(grade)
    if state.get("batch_ahead"):
        #   ★ **한 번만** 적는다. 날짜마다 되풀이하면 같은 말이 답을 덮는다.
        lines += ([""] if lines else []) + [AHEAD_BATCH]
        grades.append("empty")
    return "\n".join(lines), qa_scope.grade(grades)


def _batch_block(block: dict[str, Any]) -> tuple[list[str], str]:
    """하루치 배치 글. 돌려주는 것은 (줄 목록, 성적)."""
    on = block["on"]
    lines = [f"**배치 — {on}**", ""]
    grades: list[str] = []

    row = block.get("row")
    if block.get("read") == "error":
        lines.append("배치 기록을 읽지 못했습니다.")
        grades.append("error")
    elif not row:
        lines.append(NO_BATCH_ROW.format(on=on))
        grades.append("empty")
    else:
        status = config.BATCH_STATUS_LABEL.get(str(row.get("status")), str(row.get("status")))
        lines += [
            "| 항목 | 값 |",
            "|---|---|",
            f"| 상태 | {status} |",
            f"| 시작 | {_hhmm(row.get('started_at'))} |",
            f"| 끝 | {_hhmm(row.get('finished_at'))} |",
            f"| 끝난 단계 | {row.get('n_ok')} |",
            f"| 실패한 단계 | {row.get('n_fail')} |",
        ]
        fails = block.get("fails") or []
        if fails:
            #   ★ 실패는 **숨기지 않는다.** 단계 이름은 사람 말로 바꿔 적되,
            #     모르는 단계면 이름을 그대로 적는다 — 안 적는 것보다 낫다.
            lines += ["", "| 실패한 단계 | 남긴 말 |", "|---|---|"]
            for fail in fails:
                stage = str(fail.get("stage") or "")
                lines.append(
                    f"| {config.STAGE_LABEL.get(stage, stage)} "
                    f"| {_cell(fail.get('message'))} |"
                )
        grades.append("ok")

    report = block.get("report")
    if block.get("report_read") == "error":
        lines += ["", "점검 보고서를 읽지 못했습니다."]
        grades.append("error")
    elif not report or not report.get("body"):
        #   ★ 배치 기록도 없던 날이면 «…보고서**도** 아직 없습니다» 다 (결정 ①).
        #     조사 하나지만, 둘이 이어진 말인지 따로 선 말인지가 이걸로 갈린다.
        josa = "도" if block.get("read") == "empty" else "가"
        lines += ["", NO_CHECK_REPORT.format(on=on, josa=josa)]
        grades.append("empty")
    else:
        #   🔴 **요약하지 않는다.** 이 글은 이미 사람이 읽으라고 쓰인 것이고,
        #     다시 줄이면 우리가 고른 것만 남는다.
        lines += ["", f"**AI 점검 보고서 — {_stamp(report.get('ran_at'))}**", "",
                  str(report["body"]).strip()]
        grades.append("ok")

    return lines, qa_scope.grade(grades)


#: 보고서가 제목 맨 앞에 쓰는 가격 종류 코드. **답 문장에서는 사람 말로 바꾼다.**
_KIND_CODE = {"auc": "경락가", "whsl": "중도매가", "rtl": "소매가"}


def _kindly(title: Any) -> str:
    """«rtl 무 — …» 를 «소매가 무 — …» 로. **맨 앞 한 낱말만** 바꾼다.

    🔴 판정 문구는 손대지 않는다 (CLAUDE.md §12 · 지시 4). 바꾸는 것은 가격 종류
      코드 하나뿐이고, 그건 뜻이 아니라 이름이다 — 문장 속 다른 곳은 건드리지 않는다.
    """
    text = str(title or "")
    head, sep, rest = text.partition(" ")
    if head.lower() in _KIND_CODE:
        return _KIND_CODE[head.lower()] + sep + rest
    return text


#: 가격 종류를 못 가렸을 때. **지어내지 않는다** — 검증 보고서가 실제로 이렇다.
KIND_UNKNOWN = "(가격 종류 미상)"


def _report_kind(report: dict[str, Any]) -> str:
    """보고서 하나가 어느 가격 종류인가. **있는 것만 본다, 순서대로.**

    ```text
    ① payload.kind        다른 일꾼이 붙이는 중 — 붙으면 이게 가장 정확하다
    ② 제목 맨 앞 낱말      auc · whsl · rtl        (판정 보고서에만 있다)
    ③ 없으면 «(가격 종류 미상)»
    ```

    🔴 ③ 을 추측으로 메우지 않는다. 2026-09-16 검증 보고서 둘은 제목이
      «견주는 창» · «배추 — …» 로 시작해 가격 종류가 **어디에도 안 적혀 있다.**
      학습 끝 날짜로 되짚으면 맞힐 수야 있지만, 그건 우리가 지어낸 것이다.
    """
    payload = report.get("payload") or {}
    code = str(payload.get("kind") or "").strip().lower()
    if code in _KIND_CODE:
        return _KIND_CODE[code]
    for finding in payload.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        head = str(finding.get("title") or "").partition(" ")[0].lower()
        if head in _KIND_CODE:
            return _KIND_CODE[head]
    return KIND_UNKNOWN


def _numbers_of(report: dict[str, Any]) -> list[dict[str, str]]:
    """보고서 안의 수치 묶음을 `{이름: 값}` 으로. 짝이 아닌 것은 버린다."""
    out: list[dict[str, str]] = []
    for finding in (report.get("payload") or {}).get("findings") or []:
        if not isinstance(finding, dict):
            continue
        out.append({
            str(pair[0]): str(pair[1])
            for pair in finding.get("numbers") or []
            if len(pair) >= 2
        })
    return out


#: 교체 직후라 견줄 것이 없을 때 붙이는 줄. **«문제 없음» 이 아니다.**
SAME_TRAIN_END = (
    "현행과 후보의 학습 끝이 같습니다 — 교체 직후라 견줄 새 후보가 없습니다"
)


def _same_train_end(report: dict[str, Any]) -> bool:
    """현행과 후보의 학습 끝이 같은가. **payload 에서 정확히 읽힐 때만** 참이다.

    2026-09-16 소매가 검증이 그 경우다 — 둘 다 2025-12-31 이고 WMAPE 가 소수점까지
    같아 세 품목 모두 «판정 불가» 가 나왔다. 그건 모델이 이상해서가 아니라
    **견줄 새 후보가 없어서**인데, 그 말이 어디에도 안 적혀 있었다.
    """
    for values in _numbers_of(report):
        now, cand = values.get("현행 학습 끝"), values.get("후보 학습 끝")
        if now and cand:
            return now == cand
    return False


def _candidate_line(report: dict[str, Any]) -> str:
    """«재학습 후보 n개» 한 줄. **글자 그대로** 옮긴다. 없으면 «—»."""
    for finding in (report.get("payload") or {}).get("findings") or []:
        if not isinstance(finding, dict):
            continue
        title = str(finding.get("title") or "")
        if title.startswith("재학습 후보"):
            return title
    return "—"


def _has_compare(finding: dict[str, Any]) -> bool:
    """현행과 후보를 나란히 잰 칸인가. **라벨을 지어내지 않고 있는 것만 본다.**"""
    labels = {str(pair[0]) for pair in finding.get("numbers") or [] if len(pair) >= 2}
    return "현행 WMAPE" in labels and "후보 WMAPE" in labels


def _retrain_block(report: dict[str, Any]) -> list[str]:
    """재학습 보고서 하나를 표로. **판정 문구를 고쳐 쓰지 않는다.**

    🔴 «판정 불가» 를 «문제 없음» 으로 바꾸면 안 된다 — 증명을 못 한 것과 문제가
      없는 것은 다르다 (CLAUDE.md §11). 제목과 수치를 **있는 그대로** 옮긴다.
    """
    payload = report.get("payload") or {}
    findings = [f for f in (payload.get("findings") or []) if isinstance(f, dict)]
    head = (
        f"**{report.get('name')} · {_report_kind(report)} "
        f"— {_stamp(report.get('ran_at'))} · 판정 {report.get('verdict') or '—'}**"
    )
    out = ["", head, ""]

    compare = [f for f in findings if _has_compare(f)]
    others = [f for f in findings if not _has_compare(f)]

    if compare:
        columns: list[str] = []
        for finding in compare:
            for pair in finding["numbers"]:
                if str(pair[0]) not in columns:
                    columns.append(str(pair[0]))
        out.append("| 대상 | " + " | ".join(columns) + " | 판정 |")
        out.append("|" + "---|" * (len(columns) + 2))
        for finding in compare:
            values = {str(p[0]): str(p[1]) for p in finding["numbers"] if len(p) >= 2}
            target, _, verdict = _kindly(finding.get("title")).partition(" — ")
            cells = [_cell(target)] + [_cell(values.get(c, "—")) for c in columns]
            cells.append(_cell(verdict or finding.get("level")))
            out.append("| " + " | ".join(cells) + " |")
        out.append("")

    if others:
        out += ["| 항목 | 수치 |", "|---|---|"]
        for finding in others:
            numbers = " · ".join(
                f"{p[0]} {p[1]}" for p in finding.get("numbers") or [] if len(p) >= 2
            )
            out.append(f"| {_cell(_kindly(finding.get('title')))} | {_cell(numbers) or '—'} |")
    if compare and _same_train_end(report):
        #   ★ 표 **아래** 한 줄. 숫자를 먼저 보이고 왜 그런지를 뒤에 붙인다.
        out += ["", f"> {SAME_TRAIN_END}."]
    return out


def _day(value) -> str:
    """날짜 한 칸. 시각이 붙어 있어도 **날짜만** 적는다."""
    if value is None:
        return "—"
    try:
        return value.strftime("%Y-%m-%d")
    except (AttributeError, ValueError, TypeError):
        return str(value)


def _minute(value) -> str:
    """교체 시각. `swapped_at` 은 **이미 한국 시간**이라 돌리지 않는다."""
    if value is None:
        return "—"
    try:
        return value.strftime("%Y-%m-%d %H:%M")
    except (AttributeError, ValueError, TypeError):
        return str(value)


#: 교체 이력을 못 읽었을 때 «최근 교체» 칸에 적을 말.
_SWAP_CELL = {"absent": "교체 이력 없음", "error": "교체 이력을 읽지 못했습니다"}


def _models_table(state: QaState) -> list[str]:
    """**지금 무엇이 도나.** 이름 · 만든 날 · 학습 끝 · 최근 교체.

    🔴 **이름만으로는 알 수 없다.** `ops_rtl` 은 모델을 갈아 끼워도 그대로다 —
      매입 파트 필터가 이름 정확히 일치라 바꾸면 에러 없이 0건이 된다.
      2026-09-15 저녁에 소매가 모델이 바뀌었는데 답에 그 사실이 한 글자도
      안 남아 있었다. 그래서 **만든 날**을 같이 적는다.

    ★ 모델 이름(`ops_rtl`)은 코드 이름이지만 **사용자가 보여 달라고 한 것**이라
      그대로 적는다. 답 문장에서 코드 이름을 빼는 규칙의 예외다.
    """
    if state.get("models_read") == "error":
        return ["**현재 모델**", "", "현재 모델을 읽지 못했습니다."]

    models = state.get("models") or []
    if not models:
        return ["**현재 모델**", "", "현재 모델 기록이 없습니다."]

    cutover = state.get("cutover_read") or "ok"
    lines = [
        "**현재 모델**",
        "",
        "| 가격 | 이름 | 만든 날 | 학습 끝 | 최근 교체 |",
        "|---|---|---|---|---|",
    ]
    for row in models:
        kind = str(row.get("kind") or "")
        label = KIND_LABEL.get(kind, kind)
        swapped = row.get("last_swapped_at")
        if swapped is not None:
            #   ★ **시각을 모르면 날짜만 적는다** (2026-09-16 · 결정 ⑥).
            #     백필한 행은 백업 폴더 이름에서 되짚은 것이라 날짜만 확실한 것이
            #     있다. `00:00` 을 그대로 보이면 «한밤중에 바꿨나» 로 읽힌다 —
            #     실제로는 **모른다** 다.
            #
            #   🔴 칸이 아직 없으면(`None`) **예전 그대로** 날짜·시각을 적는다.
            #     «모른다» 와 «안 물어봤다» 를 같은 모양으로 내보내지 않는다.
            swap = (
                f"{_day(swapped)} (시각 미상)"
                if row.get("last_swap_time_known") is False
                else _minute(swapped)
            )
        else:
            #   «표가 아직 없다» 와 «표는 있는데 이 종류가 안 바뀐 적이 없다» 를
            #   섞지 않는다. 앞은 안내, 뒤는 사실이다.
            swap = _SWAP_CELL.get(cutover, "교체 기록 없음")
        lines.append(
            f"| {label} | {_cell(row.get('model_ver'))} "
            f"| {_day(row.get('created_at'))} | {_day(row.get('train_end'))} | {swap} |"
        )
    #   🔴 **꼬리말(`note`)은 안 적는다** (2026-09-16 · 결정 ⑥).
    #     실제 `note` 가 300자가 넘어 답이 꼬리말 세 줄로 덮였다. 칸에 넣었다가
    #     표 밖으로 내렸다가, 결국 뺐다. 그것이 말하려던 «시각이 추정이다» 는
    #     «(시각 미상)» 이 대신하고 **원문은 `model_cutover.note` 에 그대로 있다.**
    return lines


#: 콘솔에 못 물었을 때 적는 한 줄. **«후보 없음» 과 다른 말이다** — 앞은
#: 「모르겠다」, 뒤는 「봤는데 없다」다. 섞으면 콘솔이 죽은 동안 사람이
#: «바꿀 것이 없구나» 로 읽는다.
UPDATE_UNREADABLE = "업데이트 후보 확인 불가 (ML 콘솔 연결 안 됨)."

#: 버튼을 마크다운에 싣는 방법. **평범한 링크가 아니라 `action:` 스킴**이다.
#:
#: ★ 채팅으로 가는 길에 살아남는 것은 `answer_markdown` **한 덩어리뿐**이다
#:   (`master/domain/answer.py::_MARKDOWN_AGENTS`). 나머지 payload 칸은 마스터가 사실
#:   줄로 펴 버려 화면 거품 안으로 «구조» 가 못 들어온다. 그래서 버튼을 글 안에
#:   싣는다 — 화면(`ml/Markdownish.tsx`)이 이 스킴만 버튼으로 그린다.
#:
#: 🔴 **경로를 여기서 지어내지 않는다.** 누르면 재학습 탭이 쓰는 그 함수
#:   (`lib/mlConsole.ts::graphAct(kind, "apply")`)가 그대로 돈다.
UPDATE_ACTION = "action:retrain-apply?kind={kind}"


def _candidate_train_end(state: QaState, label: str) -> str:
    """후보가 **어디까지 배웠나**. 그날 검증 보고서에 적혀 있을 때만 돌려준다.

    🔴 `/retrain/pending` 에는 이 값이 **없다** (2026-09-16 실측 · 그 행은
      `kind` · `state` · `sec` · `candidate` · `items` · `verify` 뿐이고,
      같이 오는 `current_models` 의 `train_end` 는 **현행** 것이다).
      후보 이름 `ops_rtl_cand_20260916` 의 날짜는 **만든 날**이지 학습 끝이
      아니다. 그걸로 되짚으면 맞을 때도 있지만 그건 우리가 지어낸 것이다.

    그래서 «후보 학습 끝» 이라고 **적혀 있는 곳**에서만 가져오고, 없으면
    빈 글자를 돌려준다 — 부르는 쪽이 괄호째 뺀다.
    """
    for report in state.get("retrain_rows") or []:
        if _report_kind(report) != label:
            continue
        for values in _numbers_of(report):
            end = values.get("후보 학습 끝")
            if end:
                return str(end)
    return ""


def _update_lines(state: QaState) -> list[str]:
    """«바꿀 수 있는 후보가 있다» 와 그 버튼. **없으면 한 줄도 안 적는다.**

    ★ 자리는 **답의 맨 아래**다 (`_perf_section` 머리말). 현재 모델 · 성능 아홉 칸 ·
      재학습 판정과 검증 표를 다 보인 **뒤에** 묻는다 — 누를지 정할 근거를 보기
      전에 버튼이 먼저 나오면 안 된다.
    """
    read = state.get("pending_read")
    if read is None:
        return []                                 # 성능 갈래가 아니다 — 물어본 적도 없다
    if read == "error":
        return ["", UPDATE_UNREADABLE]

    out: list[str] = []
    for row in state.get("pending") or []:
        kind = str(row.get("kind") or "").strip().lower()
        label = _KIND_CODE.get(kind)
        if not label:
            #   버튼을 만들 수 없는 종류다. 누를 수 없는 버튼을 그리지 않는다.
            continue
        end = _candidate_train_end(state, label)
        when = f" (학습 끝 {end})" if end else ""
        out += [
            "",
            f"**{label} 후보{when} — 현행보다 나음 · 업데이트할 수 있습니다**",
            "",
            f"[모델 업데이트 — {label}]({UPDATE_ACTION.format(kind=kind)})",
        ]
    return out


def _judge_table(reports: list[dict[str, Any]]) -> list[str]:
    """재학습 **판정**은 한 줄씩. 펼치면 하루 세 건이라 답을 덮는다.

    🔴 줄이되 **판정 문구는 글자 그대로** 옮긴다 (CLAUDE.md §12). «재학습 후보 2개»
      는 우리가 센 것이 아니라 보고서가 쓴 말이다.
    """
    if not reports:
        return []
    out = [
        "",
        f"**재학습판정 — {len(reports)}건**",
        "",
        "| 가격 | 시각 | 판정 | 후보 |",
        "|---|---|---|---|",
    ]
    for report in reports:
        out.append(
            f"| {_report_kind(report)} | {_stamp(report.get('ran_at'))} "
            f"| {_cell(report.get('verdict')) or '—'} | {_cell(_candidate_line(report))} |"
        )
    return out


def _perf_section(state: QaState, asked_on: date) -> tuple[str, str]:
    """성능 갈래의 글. **현재 모델 + 봉인 개봉 아홉 칸 + 그날 재학습 이야기.**

    ★ 조건을 값과 **떼어 놓지 않는다** (CLAUDE.md §11). 표 제목에 언제·무엇으로
      잰 값인지가 늘 같이 간다.

    ★ 순서가 뜻이다 — **지금 무엇이 도나**를 먼저 보이고, 그 다음이 그 모델이
      얼마나 맞히나, 그 다음이 그날 무엇을 견줬나, **맨 마지막이 그래서 바꿀
      것이 있나**다.

    🔴 **업데이트 줄과 버튼은 맨 아래다** (2026-09-16 · 사용자 지시). 처음에는
      «현재 모델» 표 바로 아래에 뒀는데, 그러면 **누를지 정할 근거(검증 표)를
      보기 전에 버튼이 먼저 나온다.** 숫자를 다 보이고 나서 묻는다.

    ★ **나가는 문이 하나다.** 재학습 기록을 못 읽어도 그 줄 다음에 버튼이 붙게
      중간에서 빠져나가지 않는다 — 갈라 두면 한쪽에만 버튼이 붙는다.
    """
    lines = _models_table(state)
    lines += [
        "",
        f"**모델 성능 — {config.SEALED_SOURCE}**",
        "",
        "| 품목 | 가격 | 평균 실제가 | 평균 오차 | 오차율 |",
        "|---|---|---|---|---|",
    ]
    for (kind, item), cell in config.SEALED_ACCURACY.items():
        lines.append(
            f"| {item} | {KIND_LABEL.get(kind, kind)} "
            f"| {cell['avg']} | {cell['err']} | {cell['pct']}% |"
        )

    if state.get("perf_trimmed") and "batch" not in (state.get("routes") or []):
        lines += ["", _trimmed_line(state.get("perf_days") or [])]

    if state.get("perf_read") == "error":
        lines += ["", "재학습 기록을 읽지 못했습니다."]
    else:
        rows = state.get("retrain_rows") or []
        if not rows:
            #   ★ **«기록이 없다» 와 «후보가 없다» 는 다르다** (2026-09-16 · 결정 ①).
            #     전에는 둘을 한 문장(«재학습 후보 없음»)으로 적었다. 그러면 배치가
            #     아예 안 돈 날도 «검사해 봤더니 바꿀 게 없다» 로 읽힌다.
            days = state.get("perf_days")
            if days is None:
                days = [asked_on]
            #   ★ 앞날만 물었으면 읽을 날이 **하나도 없다.** 그때 오늘을 끼워 넣으면
            #     안 물어본 날의 «기록 없음» 이 나간다 (2026-09-16).
            if days:
                lines += ["", NO_RETRAIN_ROW.format(on=" · ".join(str(d) for d in days))]
        else:
            #   ★ **판정은 요약, 검증은 표 전부** (2026-09-16 · 되물음 ① 결정).
            #     검증은 «현행 vs 후보» 숫자가 판단의 근거라 줄이면 못 읽는다.
            lines += _judge_table([r for r in rows if r.get("name") == "재학습판정"])
            for report in rows:
                if report.get("name") != "재학습판정":
                    lines += _retrain_block(report)

    if state.get("perf_ahead"):
        #   ★ 배치와 같은 규칙이다 — **봉인 성능표·현재 모델은 그대로** 나간다.
        #     그건 날짜와 무관하고, 없는 것은 그날 재학습 기록뿐이다.
        lines += ["", AHEAD_PERF]

    #   ★ **맨 아래.** 숫자를 다 보인 뒤에 «그래서 바꿀까요» 를 묻는다
    lines += _update_lines(state)
    return "\n".join(lines), "ok"


def compose_answer(state: QaState, asked_on: date) -> QaState:
    """유일한 출구. 정상 답·되묻기·거절·배치·성능이 **같은 자리에서** 나간다.

    :param asked_on: 물어본 날(화면 기준일, 없으면 서울 오늘). 배치 · 성능 글이 날짜 목록을
        못 받았을 때 쓴다 — 그래프의 `compose` 노드가 정해 넘긴다.

    ★ 갈래를 **부른 순서대로** 이어 붙인다. 「5일 뒤 배추 경락가랑 배치 상태」면
      가격 표가 먼저고 배치가 뒤다 — 물어본 차례가 답의 차례다.
    """
    routes = state.get("routes") or ["forecast"]
    parts: list[str] = []
    grades: list[str] = []
    forecast_status: str | None = None
    meta: QaMeta | None = None

    for route in routes:
        if route == "forecast":
            if state.get("status") and not state.get("blocks"):
                forecast_status = state["status"]
                meta = QaMeta(
                    status=forecast_status,                  # type: ignore[arg-type]
                    item=state.get("item"),
                    kind=state.get("kind"),
                    items=state.get("items") or [],
                    kinds=state.get("kinds") or [],
                    base_dt=state.get("base_dt"),
                    out_of_range=state.get("out_of_range") or [],
                )
                parts.append(state.get("message", ""))
                grades.append(qa_scope.FORECAST_GRADE.get(forecast_status, "ok"))
            else:
                built = _answer_markdown(state)
                forecast_status = built["status"]
                meta = built["meta"]
                parts.append(built["markdown"])
                grades.append("empty" if forecast_status == "NO_DATA" else "ok")
        elif route == "batch":
            body, grade = _batch_section(state, asked_on)
            parts.append(body)
            grades.append(grade)
        elif route == "perf":
            body, grade = _perf_section(state, asked_on)
            parts.append(body)
            grades.append(grade)

    if meta is None:
        meta = QaMeta(status="OK", base_dt=state.get("base_dt"))
    meta.status = qa_scope.combined_status(routes, forecast_status, grades)  # type: ignore[assignment]
    meta.routes = list(routes)
    note = [state["note"], ""] if state.get("note") else []
    return {
        "markdown": "\n\n".join(note + [p for p in parts if p]).strip(),
        "meta": meta,
        "status": meta.status,
    }


def _line(label: str, row: dict[str, Any], unit: str, note: str = "") -> str:
    if row.get("is_filled"):
        note = (note + " · " if note else "") + "휴일의 경우 직전 예측값을 사용합니다."
    #   ★ 「모델 대신 출발점을 그대로 씀」은 **문장에서 뺐다** (2026-09-15 · 화면에서 발견).
    #     표 아래 설명 줄(출발점)을 뺄 때 비고 칸의 이 문구를 놓쳤다. 출발점이라는 말을
    #     화면에서 없앴는데 비고에만 남아 뜻 모를 말이 됐다. 값은 meta.is_gated 로 간다.
    return (
        f"| {label} | **{int(row['predicted']):,}{unit}** | "
        f"{int(row['lower']):,} ~ {int(row['upper']):,} | {note} |"
    )


def _block_table(block: dict[str, Any], state: QaState, many: bool) -> tuple[list[str], str]:
    """조합 하나를 표로. 돌려주는 것은 (줄 목록, 단위)."""
    item, kind = block["item"], block["kind"]
    rows, today = block["rows"], block["today"]
    base_dt = state["base_dt"]
    unit = (rows[0]["unit"] if rows else (today or {}).get("unit")) or "원/kg"

    as_of = state["request"].as_of or base_dt
    out = [
        f"**{item} · {KIND_LABEL.get(kind, kind)} · 기준일 {as_of}**",
        "",
        "| 날짜 | 예측 | 예상 구간 | 비고 |",
        "|---|---|---|---|",
    ]
    if today:
        out.append(_line(f"오늘 {today['target_dt']}", today, unit))
    for row in rows:
        #   화면 기준일과 같은 날이면 「오늘」로 적는다 — 예측일이 하루 앞서 계산된
        #   날(휴일 뒤 등)에도 사람이 보는 「오늘」은 화면 날짜다.
        if row["target_dt"] == as_of:
            label = f"오늘 {row['target_dt']}"
        else:
            label = f"{row['target_dt']} (D+{(row['target_dt'] - as_of).days})"
        out.append(_line(label, row, unit))

    #   ★ **설명 줄을 문장에 안 적는다** (2026-09-15 · 화면을 깨끗이 하라는 지시).
    #     출발점 · 평균 오차 · 값의 정체는 `meta` 로 옮겼다 — 없앤 것이 아니다.
    #
    #   ★ **«쓰지 마세요» 경고도 뺐다** (2026-09-16 · 사용자 결정 ⑦). 마지막까지
    #     문장에 남겨 두었던 한 줄인데, 가격 답은 표만 깔끔하게 두기로 했다.
    #
    #   🔴 **없앤 것이 아니라 옮긴 것이다.** `use_recommended` · `quality_note` ·
    #     `is_gated` 는 `meta` 와 마스터 payload 에 그대로 간다 — **판단하는 쪽이
    #     저기다.** 문장에서 빼는 것과 값에서 지우는 것은 전혀 다른 일이다.
    if many:
        out.append("")
    return out, unit


def _first_of(block: dict[str, Any], key: str) -> Any:
    """블록의 첫 행에서 한 칸. 없으면 당일 값에서, 그것도 없으면 규격표에서.

    🔴 **당일 행에는 규격 칸이 아예 없다** (2026-09-15 실측). 원본 창고는
      `market_name` · `grade_name` · `spec_desc` 를 담지 않는다. 그래서 오늘 값만
      답할 때 규격이 통째로 비었다 — 문장에서 뺀 값이 **정말로 사라진** 것이다.

      `SPEC` 은 우리가 쥔 상수이므로 거기서 채운다. 지어내는 것이 아니라
      **같은 사실을 다른 자리에서** 가져오는 것이다.
    """
    rows = block.get("rows") or []
    if rows and rows[0].get(key) is not None:
        return rows[0][key]
    today = block.get("today") or {}
    if today.get(key) is not None:
        return today[key]
    spec = SPEC.get(block.get("kind") or "") or {}
    if key == "market_name":
        return spec.get("market")
    if key == "grade_name":
        return spec.get("grade")
    if key == "spec_desc":
        return (spec.get("desc") or {}).get(block.get("item"))
    return None


def _filled_line(filled: list[str], blocks: list[dict[str, Any]]) -> str:
    """무엇을 기본값으로 채웠는지 한 줄. **채운 것만 적는다.**

    ★ 실제로 답한 것을 적는다 — 상수 목록이 아니라 블록에서 뽑는다. 둘이 어긋나면
      «전부 보여드립니다» 라고 써 놓고 일부만 나가는 답이 된다.
    """
    said: list[str] = []
    if "품목" in filled:
        names = list(dict.fromkeys(b["item"] for b in blocks))
        said.append("**" + " · ".join(names) + "**")
    if "가격 종류" in filled:
        names = list(dict.fromkeys(KIND_LABEL.get(b["kind"], b["kind"]) for b in blocks))
        said.append("**" + " · ".join(names) + "**")
    #   ★ 조사를 붙여 둔다 — «품목를» 이 나오면 사람이 먼저 그걸 본다.
    what = "품목과 가격 종류를" if len(filled) > 1 else (
        "품목을" if filled == ["품목"] else "가격 종류를"
    )
    return f"{what} 말씀하지 않으셔서 {'의 '.join(said)}를 전부 보여드립니다."


def _answer_markdown(state: QaState) -> QaState:
    """조합마다 표 하나. **공통 안내는 한 번만** 적는다."""
    base_dt = state["base_dt"]
    blocks = state.get("blocks") or []
    many = len(blocks) > 1

    all_rows = [r for b in blocks for r in b["rows"]]
    todays = [b["today"] for b in blocks if b["today"]]
    missing = sorted({
        d
        for block in blocks
        for d in (set(block.get("targets") or []) - {r["target_dt"] for r in block["rows"]})
    })

    head: list[str] = []
    filled = state.get("filled_defaults") or []
    if filled:
        #   ★ **무엇을 기본값으로 채웠는지 한 줄로 밝힌다** (2026-09-16 · 사용자 지시).
        #     되묻는 대신 전부 보여주기로 했으니, 사람이 «왜 아홉 개가 나오지» 를
        #     묻지 않게 그 자리에서 말한다. 군더더기는 이 한 줄뿐이다.
        head = [_filled_line(filled, blocks), ""]
    elif many:
        #   ★ 여러 조합이면 무엇을 답했는지 맨 앞에 밝힌다 — 표가 길어 눈에 안 들어온다.
        names = " · ".join(
            f"{b['item']} {KIND_LABEL.get(b['kind'], b['kind'])}" for b in blocks
        )
        head = [f"**{names}** 를 모두 보여드립니다.", ""]

    body: list[str] = []
    for block in blocks:
        lines, _unit = _block_table(block, state, many)
        body += lines

    tail: list[str] = [""]
    if state.get("used_default"):
        #   ★ **기본값을 썼다는 것을 밝힌다.** 안 밝히면 「하루치만 있나 보다」로 읽힌다.
        tail.append(
            "> 날짜를 따로 말씀하지 않으셔서 "
            + ("오늘 값이 아직 없어 **내일** 값을 보여드립니다."
               if state.get("default_fell_back") else "**오늘** 값을 보여드립니다.")
            + f" {ASK_DATE_HINT}"
        )
    if state.get("out_of_range"):
        last = base_dt + timedelta(days=QA_MAX_OFFSET)
        days = " · ".join(str(d) for d in state["out_of_range"])
        tail.append(
            #   ★ 오늘 값도 답하므로 시작은 **오늘**이다 (범위 밖만 물었을 때 안내와 맞춘다).
            f"> ⚠ {days} 은 예측 범위 밖입니다. "
            f"{state['request'].as_of or base_dt} 부터 {last} 까지 답할 수 있습니다."
        )
    if missing:
        tail.append(f"> ⚠ {' · '.join(str(d) for d in missing)} 은 그 기준일에 예측이 없습니다.")
    if all_rows:
        #   ★ **코드 이름을 문장에 넣지 않는다** (마스터 요청 · 2026-09-15).
        tail.append("\n*" + _kst(all_rows[0]["generated_at"]) + " 에 계산한 값입니다*")

    status = "OK"
    if state.get("out_of_range") or missing:
        status = "PARTIAL"
    if not all_rows and not todays:
        status = "NO_DATA"

    first = blocks[0] if blocks else {}
    meta = QaMeta(
        status=status,
        item=first.get("item"),
        kind=first.get("kind"),
        items=[b["item"] for b in blocks],
        kinds=[b["kind"] for b in blocks],
        base_dt=base_dt,
        targets=([r["target_dt"] for r in (first.get("rows") or [])]
                 + ([first["today"]["target_dt"]] if first.get("today") else [])),
        missing=missing,
        out_of_range=state.get("out_of_range") or [],
        model_version=(all_rows[0]["model_version"] if all_rows
                       else (todays[0] if todays else {}).get("model_version")),
        generated_at=_kst(all_rows[0]["generated_at"]) if all_rows else None,
        source=("ml_price_forecasts · prediction_log" if (all_rows and todays)
                else "prediction_log" if todays else "ml_price_forecasts"),
        is_filled=[bool(r.get("is_filled")) for r in (first.get("rows") or [])],
        is_gated=[bool(r.get("is_gated")) for r in (first.get("rows") or [])],
        band_method=(all_rows[0].get("band_method") if all_rows
                     else (todays[0] if todays else {}).get("band_method")),
        use_recommended=qa_scope.all_usable(blocks),
        #   ★ 문장에서 뺀 값들 — 여기로 옮겼다. 없앤 것이 아니다.
        current_price=_first_of(first, "current_price"),
        accuracy_pct=(first.get("accuracy") or {}).get("pct"),
        accuracy_note=config.SEALED_SOURCE if first.get("accuracy") else None,
        market_name=_first_of(first, "market_name"),
        grade_name=_first_of(first, "grade_name"),
        spec_desc=_first_of(first, "spec_desc"),
    )
    #   ★ 무엇을 무시했는지는 **compose 가** 답 맨 앞에 적는다 (2026-09-16).
    #     갈래가 여럿이면 그 줄은 가격 표 앞이 아니라 **답 전체 앞**에 와야 한다.
    return {
        "markdown": "\n".join(head + body + tail).rstrip(),
        "meta": meta,
        "status": status,
    }


def answer_from(final: dict[str, Any]) -> QaAnswer:
    """마친 그래프 상태 → `QaAnswer`. **읽은 행을 같이 돌려준다** (2026-09-15).

    마스터 어댑터가 회신에 붙일 `Evidence` 를 그 행에서 만든다 — 답 문장에서 숫자를 다시
    뜯어내면 **같은 사실에 두 경로가 생긴다.** 값은 표에서 온 것 하나여야 한다.
    """
    #   ★ 조합이 여럿이면 **행마다 어느 조합인지** 붙인다 (2026-09-15).
    #     안 붙이면 어댑터가 근거를 만들 때 배추 경락가와 배추 중도매가를 못 가린다.
    rows: list[dict] = []
    for block in final.get("blocks") or []:
        for row in [*block["rows"], *([block["today"]] if block["today"] else [])]:
            rows.append({**row, "item": block["item"], "kind": block["kind"]})
    return QaAnswer(
        markdown=final["markdown"],
        meta=final["meta"],
        rows_for_evidence=rows,
        batch_for_evidence=_batch_evidence(final),
        performance_for_evidence=_performance_evidence(final),
        models_for_payload=_models_payload(final),
        reads=_reads(final),
    )


def _reads(final: dict[str, Any]) -> dict[str, str]:
    """갈래마다 **어느 표를 어떻게 읽었나**. 키는 표 이름이다.

    🔴 **물어본 갈래의 표만 적는다** (2026-09-16 · 사용자 결정 ①). 안 물어본 갈래의
      표 이름이 «없는 것» 목록에 끼면, 고치러 간 사람이 멀쩡한 표를 들여다본다.

    ★ `model_cutover` 의 `absent`(표가 아직 없다)는 **실패가 아니다.** 두 창고 다
      그 표가 없는 상태로 잘 돌고 있다 — `ok` 와 같이 둔다.
    """
    routes = final.get("routes") or []
    out: dict[str, str] = {}
    if "batch" in routes:
        out["batch_run"] = final.get("batch_read") or "empty"
        out["agent_report"] = final.get("check_read") or "empty"
    if "perf" in routes:
        #   재학습 보고서가 **없는 날**은 «없다» 다 — 못 읽은 것과 가른다.
        retrain = (
            "error" if final.get("perf_read") == "error"
            else "ok" if final.get("retrain_rows") else "empty"
        )
        out["agent_report"] = qa_scope.worst([out.get("agent_report", retrain), retrain])
        out["prediction_log"] = (
            "error" if final.get("models_read") == "error"
            else "ok" if final.get("models") else "empty"
        )
        out["model_cutover"] = "error" if final.get("cutover_read") == "error" else "ok"
    return out


def _models_payload(final: dict[str, Any]) -> list[dict[str, Any]]:
    """«현재 모델» 을 기계용 칸 모양으로. **날짜는 글자로** 낸다 (JSON 으로 나간다).

    ★ 못 읽었으면 **빈 목록**이다. 어댑터가 그때 칸을 아예 안 만든다 —
      «안 읽었다» 와 «비어 있다» 를 같은 모양으로 내보내지 않는다.
    """
    if "perf" not in (final.get("routes") or []):
        return []
    return [
        {
            "kind": row.get("kind"),
            "model_ver": row.get("model_ver"),
            "created_at": _day(row.get("created_at")) if row.get("created_at") else None,
            "train_end": _day(row.get("train_end")) if row.get("train_end") else None,
            "last_swapped_at": (
                _minute(row.get("last_swapped_at")) if row.get("last_swapped_at") else None
            ),
            #   ★ 시각을 아는가. `None` 은 «칸이 아직 없다» 지 «모른다» 가 아니다.
            "last_swap_time_known": row.get("last_swap_time_known"),
        }
        for row in final.get("models") or []
    ]


def _batch_evidence(final: dict[str, Any]) -> dict[str, Any] | None:
    """배치 갈래가 읽은 것을 어댑터가 쓸 모양으로. **안 물었으면 `None`.**

    🔴 «못 읽었다» 와 «없다» 를 `read` 칸으로 갈라 둔다. 하나로 뭉치면 마스터
      이력에서 창고가 죽은 날과 배치가 안 돈 날이 같아 보인다.
    """
    if "batch" not in (final.get("routes") or []):
        return None
    row = final.get("batch_row") or {}
    report = final.get("check_row") or {}
    return {
        "on": final.get("batch_on"),
        "read": final.get("batch_read"),
        "run_id": row.get("run_id"),
        "status": config.BATCH_STATUS_LABEL.get(
            str(row.get("status")), str(row.get("status"))
        ) if row else None,
        "n_ok": row.get("n_ok"),
        "n_fail": row.get("n_fail"),
        "report_read": final.get("check_read"),
        "report_ran_at": _stamp(report.get("ran_at")) if report else None,
    }


def _performance_evidence(final: dict[str, Any]) -> list[dict[str, Any]]:
    """봉인 개봉 아홉 칸. **조건(`SEALED_SOURCE`)은 어댑터가 근거에 적는다.**"""
    if "perf" not in (final.get("routes") or []):
        return []
    return [
        {
            "item": item,
            "kind": kind,
            "avg_price": cell["avg"],
            "avg_error": cell["err"],
            #   ★ 오차율만 숫자로 낸다 — 근거를 붙일 수 있는 칸이 이것뿐이다
            #     (`Evidence.value` 는 수치다).
            "pct": float(cell["pct"]),
        }
        for (kind, item), cell in config.SEALED_ACCURACY.items()
    ]
