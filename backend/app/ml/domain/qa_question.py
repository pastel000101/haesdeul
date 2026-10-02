"""예측 질의응답 — 무엇을 물었나를 가리는 판단 (입력 → 출력만).

요청에 직접 준 값(`item`·`kind`·`dates`)과 해석기(`llm/qa.py`)가 고른 값을 받아, 그래프가
들고 갈 갈래 · 품목 · 가격 종류 · 날짜를 정한다. 되묻기 · 거절 문구도 여기 있다.

DB · LLM · 시계를 부르지 않는다. 기준일을 읽고 해석기를 부르는 것은 그래프의
`supervise` 노드(`service/qa_graph.py`)이고, 여기는 그 결과를 받아 판단만 한다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.ml.config import KIND_LABEL
from app.ml.schemas.qa import QA_ITEMS, QA_KINDS, QA_ROUTES, QaRequest
from app.ml.schemas.qa_state import QaState

#: 되묻기·거절 문구는 틀에 박는다. 문장이 짧아질 때 먼저 잘리면 안 되는 자리다.
OUT_OF_SCOPE_ITEM = (
    "**{item}** 은 우리 예측 대상이 아닙니다. 배추 · 무 · 양파만 답할 수 있습니다."
)
#: 품목 이름을 못 받았을 때. LLM 이 out_of_scope 만 고르고 item 을 비워 보내는 일이 있다.
OUT_OF_SCOPE_UNKNOWN = (
    "우리가 답할 수 있는 품목이 아닙니다. 배추 · 무 · 양파만 답할 수 있습니다."
)
OUT_OF_SCOPE_KIND = (
    "가격 종류를 알 수 없습니다. **경락가(AUC) · 중도매가(WHSL) · 소매가(RTL)** 중에서 "
    "골라 다시 물어봐 주세요."
)
NEED_CLARIFY_LLM = (
    "지금 질문을 해석하지 못했습니다. 품목과 가격 종류를 넣어 다시 물어봐 주세요.\n\n"
    "- `배추 경락가 내일 얼마야?`\n"
    "- `무 소매가 5일 뒤 얼마나 바뀌어?`"
)
SOURCE_DOWN = "지금 예측 창고를 읽지 못했습니다. 값을 지어내지 않기 위해 답을 비웁니다."

#: 전달표에 예측이 한 건도 없을 때. `supervise` 와 `gate` 두 자리가 같은 문장을 쓴다.
NO_FORECAST_YET = "전달표에 예측이 아직 없습니다."


def choose_without_question(req: QaRequest) -> QaState | None:
    """질문 문장 없이도 정해지는 경우. 해석기를 불러야 하면 `None` 이다.

    ```text
    item · kind 를 둘 다 우리 값으로 줬다   가격 갈래 · 준 값 그대로
    질문이 없다                             범위 밖 품목이면 거절 · 아니면 빈 자리를 전부로 채운다
    그 밖                                   None — 기준일을 읽고 해석기에 묻는다
    ```
    """
    #   직접 준 값이 우리 품목일 때만 해석을 건너뛴다.
    #   Swagger 기본 본문이 item 에 "string" 을 넣어 주는데, 그것을 품목으로 읽고
    #   거절하면 질문 문장을 쳐다보지도 않는다(2026-09-14 실측).
    given_items = valid(req.items or ([req.item] if req.item else []), QA_ITEMS)
    given_kinds = valid(req.kinds or ([req.kind] if req.kind else []), QA_KINDS)
    if given_items and given_kinds:
        #   값을 직접 준 길은 늘 가격 갈래다. 배치·성능은 질문 문장으로만 온다.
        return {"routes": ["forecast"],
                "items": given_items, "kinds": given_kinds,
                "item": given_items[0], "kind": given_kinds[0]}
    if not req.question:
        if req.item and req.item not in QA_ITEMS:
            #   범위 밖은 전부로 안 바꾼다. 「대파」를 물었는데 배추가 나가면
            #   물은 것과 다른 답이다.
            return {"routes": ["forecast"], "status": "OUT_OF_SCOPE",
                    "message": OUT_OF_SCOPE_ITEM.format(item=req.item)}
        #   채팅 입구와 같은 규칙으로 채운다(사용자 결정 ④). 이 입구는 부르는 것이
        #   프로그램이라 되물어도 다시 답할 수가 없다 — 그래서 되묻지 않는다.
        return _fill_defaults({"routes": ["forecast"]}, given_items, given_kinds)
    return None


def choose_from_interpretation(
    req: QaRequest,
    chosen: dict[str, Any] | None,
    *,
    base_dt: date | None,
    source_down: bool,
) -> QaState:
    """해석기가 고른 값 → 갈래 · 품목 · 가격 종류 · 날짜.

    :param chosen: 해석기 결과. `None` 이면 해석하지 못한 것이다(키 없음 · 호출 실패).
    :param base_dt: 전달표의 `as_of` 이하 최신 기준일. 못 읽었거나 없으면 `None`.
    :param source_down: 기준일을 읽다가 실패했나 — 없는 것(`None`)과 가른다.
    """
    given_items = valid(req.items or ([req.item] if req.item else []), QA_ITEMS)
    given_kinds = valid(req.kinds or ([req.kind] if req.kind else []), QA_KINDS)

    #   질문이 같이 왔으면 질문으로 답하고, 무엇을 무시했는지 밝힌다.
    ignored = req.item if (req.item and req.item not in QA_ITEMS) else None

    if chosen is None:
        return {"routes": ["forecast"], "status": "LLM_UNAVAILABLE",
                "message": NEED_CLARIFY_LLM}

    routes, out_of_scope = _pick_routes(chosen)
    if out_of_scope and not routes:
        item = chosen.get("item")
        message = OUT_OF_SCOPE_ITEM.format(item=item) if item else OUT_OF_SCOPE_UNKNOWN
        return {"routes": ["forecast"], "status": "OUT_OF_SCOPE", "message": message}
    if not routes:
        #   아무 갈래도 못 정했다 — 갈래를 못 정해 되묻는 자리는 여기 하나다.
        #   품목·가격 종류가 빠진 것은 되묻지 않고 전부로 채운다.
        return {"routes": ["forecast"], "status": "NEED_CLARIFY",
                "message": _ask_again(None, None, chosen.get("dates"))}

    picked: QaState = {"routes": routes}
    #   고른 날짜를 갈래보다 먼저 담는다(사용자 결정 ②). 가격 갈래 안쪽에서만 담으면
    #   「어제 배치 상태」의 «어제» 가 사라져 배치 노드가 늘 오늘만 본다.
    if chosen.get("dates"):
        picked["asked"] = list(chosen["dates"])
    if base_dt is not None:
        picked["base_dt"] = base_dt
    if out_of_scope:
        #   「대파 값이랑 배치 상태」처럼 반만 우리 것인 질문이 있다.
        #   소관 밖이라고 배치 답까지 버리지 않는다 — 소관 밖이라는 말만 얹는다.
        item = chosen.get("item")
        picked["note"] = (
            OUT_OF_SCOPE_ITEM.format(item=item) if item else OUT_OF_SCOPE_UNKNOWN
        )
    if "forecast" not in routes:
        #   가격을 안 물었으면 예측표 사정은 답을 막지 않는다.
        return picked
    if source_down:
        return {**picked, "status": "SOURCE_UNAVAILABLE", "message": SOURCE_DOWN}
    if base_dt is None:
        return {**picked, "status": "NO_DATA", "message": NO_FORECAST_YET}

    if ignored:
        picked["note"] = (
            f"> `item` 에 준 «{ignored}» 는 우리 품목이 아니어서 질문 문장으로 답했습니다."
        )
    #   질문에서 못 고른 칸은 요청이 직접 준 유효한 값으로 메운다.
    #   item 하나가 엉터리라고 해서 제대로 준 kind 까지 버리면, 답할 수 있는
    #   질문에 되묻게 된다(2026-09-14 실측: item="string" · kind="AUC").
    #   짝 물음이 곧 답이다. `asks` 만 오고 `items`·`kinds` 가 비어 오면 답할 수 있는
    #   질문에 되묻게 된다 — 목록을 거기서 채운다.
    asks = chosen.get("asks") or []
    items = chosen.get("items") or [a["item"] for a in asks] or given_items
    kinds = chosen.get("kinds") or [a["kind"] for a in asks] or given_kinds

    #   짝 물음이 있으면 안 채운다 — 짝에 이미 품목·가격이 들어 있어서,
    #   거기에 전부를 끼얹으면 안 물어본 조합이 나간다.
    #   범위 밖 품목(대파 등)은 위에서 이미 갈라져 나갔다. 여기로 안 온다.
    if asks:
        picked["filled_defaults"] = []
    else:
        _fill_defaults(picked, items, kinds)
        items = picked.get("items") or items
        kinds = picked.get("kinds") or kinds

    item = items[0] if items else None
    kind = kinds[0] if kinds else None
    if items:
        picked["items"] = items
        picked["item"] = items[0]
    if kinds:
        picked["kinds"] = kinds
        picked["kind"] = kinds[0]
    if chosen.get("dates"):
        picked["asked"] = list(chosen["dates"])
    if chosen.get("asks"):
        #   짝지어진 물음이 오면 곱하지 않는다.
        #   「5일 뒤 배추 경락가와 7일 뒤 무 도매가」를 곱하면 안 물어본
        #   배추 중도매가·무 경락가가 나가고 날짜도 뒤섞인다.
        picked["asks"] = list(chosen["asks"])
    if not item or not kind:
        #   여기까지 왔는데 비어 있으면 짝 물음이 엉터리인 경우뿐이다
        #   (위에서 짝이 없을 때는 전부로 채웠다). 그때는 되묻는다.
        picked["status"] = "NEED_CLARIFY"
        picked["message"] = _ask_again(item, kind, picked.get("asked"))
    return picked


def _pick_routes(chosen: dict[str, Any]) -> tuple[list[str], bool]:
    """고른 갈래를 우리 어휘로. 돌려주는 것은 (갈래 목록, 소관 밖인가).

    옛 이름을 끊지 않는다. `routes` 가 없으면 한 칸짜리 `route` 를 읽는다 —
    그 이름으로 부르는 코드와 검사가 아직 있다.

    모르는 값(`accuracy` · `usability`)은 가격 갈래로 접는다. 되묻는 판단은 규칙이 한다.

    `clarify` 만 다르다. 그건 «못 알아들었다» 는 말이지 가격 질문이 아니다. 가격으로
    접어 버리면 품목·가격 종류를 전부로 채워 아무 말도 안 한 사람에게 아홉 개 표가
    나간다. 빈 목록을 돌려주고 부르는 쪽이 되묻게 한다.
    """
    raw = chosen.get("routes")
    if not isinstance(raw, list) or not raw:
        raw = [chosen.get("route")]
    out_of_scope = False
    clarify = False
    out: list[str] = []
    for value in raw:
        text = str(value or "").strip()
        if text == "out_of_scope":
            out_of_scope = True
            continue
        if text in _CLARIFY_WORDS:
            clarify = True
            continue
        name = text if text in QA_ROUTES else "forecast"
        if name not in out:
            out.append(name)
    if out_of_scope:
        #   소관 밖이라고 한 갈래는 가격 갈래다. 배치·성능은 품목과 무관하다.
        out = [r for r in out if r != "forecast"]
        return out, True
    if not out and clarify:
        return [], False
    return out or ["forecast"], False


#: «못 알아들었다» 는 답. 이 말만 왔으면 갈래를 못 정한 것이다.
_CLARIFY_WORDS = frozenset({"clarify", "need_clarify"})


#: 되물을 때 붙이는 한 줄. 가격만 답하는 줄 알면 다음에도 가격만 묻는다.
CAN_ALSO_ANSWER = (
    "가격 말고 **오늘 배치 결과**와 **모델 성능**도 물어보실 수 있습니다."
)


def _ask_again(item: str | None, kind: str | None, dates: list[date] | None) -> str:
    """되묻는 문장. 빠진 것만 묻고, 알아들은 것은 밝힌다."""
    known: list[str] = []
    if item:
        known.append(f"품목 **{item}**")
    if dates:
        known.append(
            f"날짜 **{dates[0]}**" if len(dates) == 1
            else f"날짜 **{dates[0]} ~ {dates[-1]}** ({len(dates)}일)"
        )

    if not item and not kind:
        need = "어느 품목의 어느 가격인지"
        example = "배추 경락가"
    elif not item:
        need = "어느 품목인지"
        example = f"배추 {KIND_LABEL.get(kind, kind)}"
    else:
        need = "어느 가격인지"
        example = f"{item} 경락가"

    lines = [f"{need} 알 수 없습니다."]
    if known:
        lines.append(f"알아들은 것 — {' · '.join(known)}. 이건 다시 안 적으셔도 됩니다.")
    if not item:
        lines.append("품목: **배추 · 무 · 양파**")
    if not kind:
        lines.append("가격: **경락가(AUC) · 중도매가(WHSL) · 소매가(RTL)**")
    lines.append(f"예: `{example}` 라고 덧붙여 다시 물어봐 주세요.")
    if not item and not kind:
        #   아무것도 못 알아들었으면 답할 수 있는 것을 다 알려준다.
        #   품목만 되물으면 «가격만 답하는 곳» 으로 읽혀 배치·성능을 영영 안 묻는다.
        lines.append(CAN_ALSO_ANSWER)
    return "\n\n".join(lines)


def _fill_defaults(picked: QaState, items: list[str], kinds: list[str]) -> QaState:
    """빠진 자리를 전부로 채운다. 무엇을 채웠는지 `filled_defaults` 에 남긴다.

    입구가 둘인데 규칙은 하나다(사용자 결정 ④). 질문 문장으로 오든(`question`) 값으로
    오든(`item`·`kind`), 빠진 쪽은 같은 식으로 채운다. 입구가 다르다고 답이 달라지면
    어느 쪽이 맞는지 알 수 없다.

    되묻지 않는다. 되물으면 사람은 한 번 더 쳐야 하고, 값으로 부르는 프로그램은 아예
    다시 답할 수가 없다. 대신 무엇을 채웠는지 답 첫 줄에 한 줄로 밝힌다
    (`readmodel/qa_answer.py` 의 `_filled_line`).
    """
    filled: list[str] = []
    if not items:
        items = list(QA_ITEMS)
        filled.append("품목")
    if not kinds:
        kinds = list(QA_KINDS)
        filled.append("가격 종류")
    picked["items"] = list(items)
    picked["item"] = items[0]
    picked["kinds"] = list(kinds)
    picked["kind"] = kinds[0]
    picked["filled_defaults"] = filled
    return picked


def valid(raw: Any, allowed: tuple[str, ...]) -> list[str]:
    """우리가 아는 값만 순서대로. 중복·빈 값은 버린다."""
    out: list[str] = []
    for value in raw or []:
        text = (value or "").strip() if isinstance(value, str) else ""
        if text in allowed and text not in out:
            out.append(text)
    return out
