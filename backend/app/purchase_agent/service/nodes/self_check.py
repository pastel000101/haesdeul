"""⑦ self_check — 사중 일치 검사 + 컷 사유 기록 + 출력 조립 (상세설계 §4-⑦).

여기서 컷된 안은 ``rejected_reasons``에 ``{label, reason}``으로 남는다. 마지막에
``revalidate_for_output()``으로 계약을 한 번 더 확인한 뒤에야 출력이 만들어진다.

여기 있는 검사는 전부 계산 검사다 — 규칙 6대로 숫자·제약은 순수 함수가 소유한다.
근거 문장을 LLM 으로 다시 읽는 일은 ⑧ ``review_rationale`` 이 하고, 그쪽은 컷 권한이 없다.

이 파일에는 노드 함수와 출력 조립(`_assemble` — 정보 요청 기능 플래그를 읽는다)이 있고,
판정 · 계산은 `domain/self_check.py` 에 있다.
"""

from typing import Any

from app.purchase_agent import AGENT_VERSION
from app.purchase_agent.config import load_constraints
from app.purchase_agent.domain.draft_plan import split_adjustments
from app.purchase_agent.domain.self_check import (
    arrival_capacity,
    check_axis_allowed,
    check_axis_diversity,
    check_cash_ceiling,
    check_document_publication,
    check_document_refs,
    check_excerpt_fidelity,
    check_max_price,
    check_payment_schedule,
    check_prices_exist,
    check_quadruple_match,
    check_split_amounts,
    check_split_dates,
    check_warehouse_capacity,
    market_open_days,
    no_proposal_reason,
    with_context_note,
)
from app.purchase_agent.domain.split_plan import effective_allowed_axes
from app.purchase_agent.features import INFORMATION_REQUESTS, enabled
from app.purchase_agent.schemas.proposal import (
    TIMING_AXIS,
    PurchaseProposal,
    document_ref,
    revalidate_for_output,
)
from app.purchase_agent.schemas.state import PurchaseAgentState


def self_check(state: PurchaseAgentState) -> dict[str, Any]:
    """안별 검사로 컷하고, 살아남은 것으로 제안을 조립해 계약을 재확인한다."""
    constraints = load_constraints()
    survivors: list[dict] = []
    rejected: list[dict] = list(state["rejected_reasons"])

    for scenario in state["scenarios_final"]:
        # 한 번만 계산한다. 컷 사유와 미검사 고지가 배타적인 두 결과라 같은 판정에서
        # 나와야 한다 — 따로 부르면 체인이 컷한 안에 "검사 안 했다"가 붙을 수 있다.
        arrival = arrival_capacity(scenario, state)
        market = market_open_days(scenario, state)
        reason = (
            check_quadruple_match(scenario)
            or check_axis_allowed(scenario, state["allowed_axes"])
            or check_prices_exist(scenario, state["market_quotes"])
            or check_max_price(scenario)
            # 창고 두 축은 붙여 둔다 — 총량이 먼저다. 총량이 이미 넘으면 날짜별
            # 사유는 부차적이고, 컷 사유는 한 안에 하나만 나간다.
            or check_warehouse_capacity(scenario, state["inventory"], state, constraints)
            or arrival.violation
            or check_cash_ceiling(scenario, state, constraints)
            or check_split_dates(scenario, state["date"])
            # 날짜 두 축도 붙여 둔다 — 순서·연속이 먼저다. 회차 자체가 어긋나 있으면
            # 「그날 장이 서나」는 부차적이고, 컷 사유는 한 안에 하나만 나간다.
            or market.violation
            or check_split_amounts(scenario)
            or check_document_refs(scenario, state["context_docs"])
            # 문서 검사 3종은 순서가 있다: 인용이 로드분인가(refs) → 발행일이 as_of 이전인가
            # (publication) → 발췌가 원문 문자인가(fidelity). 뒤 두 검사는 앞이 통과했다고
            # 가정하지 않고 각자 문서를 다시 찾는다.
            or check_document_publication(scenario, state["context_docs"], state["date"])
            or check_excerpt_fidelity(scenario, state["context_docs"])
            or check_payment_schedule(scenario, state, constraints)
        )
        if reason:
            rejected.append({"label": scenario["label"], "reason": reason})
            continue
        # 미검사 고지는 두 축(도착일 · 장 서는 날) 것을 모두 싣는다. 하나만 실으면 «둘 다
        # 못 했는데 한 줄만 보이는» 상태가 생긴다 — 안 실린 쪽은 검사한 것처럼 읽힌다.
        notes = [note for note in (arrival.skipped, market.skipped) if note]
        if notes:
            # 새 dict 를 만든다. ``state["scenarios_final"]`` 을 제자리에서 고치면
            # ⑥이 만든 값과 ⑦이 내보내는 값이 같은 객체가 되어, 나중에 둘을 대조할 수 없다.
            survivors.append({**scenario, "risks": [*scenario["risks"], *notes]})
        else:
            survivors.append(scenario)

    # ⑥과 같은 목록을 본다 — ④가 안 나눈 날의 timing 을 뺀 뒤 판정한다 (`#308`).
    effective_axes = effective_allowed_axes(state["allowed_axes"], state["split_plan"])
    # ⑥ 이 되돌린 날은 ④ 가 «진입했다» 여도 timing 을 쓴 안이 없다. 분할이 실제로 안
    # 서서 일괄로 내려온 안은 ``strategy_type`` 이 ``quantity`` 이고, 그 목록을 안 맞추면
    # «축이 둘인데 전 안 동일» 로 살아 있던 안이 통째로 반려된다 — `#308` 이 막으려던
    # 바로 그 20셀 모양이다.
    #
    # 「timing 안이 없다」만 보지 않는다. 그 안이 현금·등급 등 다른 검사에서 탈락해서
    # 없을 수도 있고, 그때 축을 빼면 "축이 둘인데 아무도 안 썼다" 라는 사실이 조용히
    # 사라진다. ⑥ 이 실제로 되돌린 라벨을 적어 보내고, 그것이 있을 때만 좁힌다.
    # 목록을 좁히기만 한다. 안 쓴 축을 빼는 것이지 검사를 끄는 것이 아니다.
    되돌린 = state.get("split_rolled_back_labels") or []
    if 되돌린 and survivors and all(s["strategy_type"] != TIMING_AXIS for s in survivors):
        effective_axes = [axis for axis in effective_axes if axis != TIMING_AXIS]
    diversity = check_axis_diversity(survivors, effective_axes)
    if diversity:
        rejected.extend({"label": s["label"], "reason": diversity} for s in survivors)
        survivors = []

    # 문서를 못 읽어도 안을 낸다 (마스터 결정: "문서 없으면 없이 진행하고, 생기면
    # 생긴대로 진행한다. 내가 통제한다.").
    #
    #   mock 을 쓰는 것과 다르다. 문서가 실제로 없어(실 소스 없음) 없이 가는 것이지,
    #   연습 데이터로 메우는 게 아니다 — `get_context_docs` 는 여전히 mock 을 막는다.
    #   없으면 없는 채로 판단하고, 그 사실만 고지한다.
    #
    #   고지는 `_assemble` 이 `context_unavailable` 을 risks 로 올린다 (컷하지 않는다).
    #
    # 무·양파는 원래 기상·작년동기 문서가 없다 — 그건 `context_unavailable` 이 아니라
    # 빈 `context_docs` 다. 둘 다 안을 막지 않는다.

    # 이 노드가 직접 컷한 것만 세어 넘긴다 — 아래 no_proposal_reason 이 원인을
    # self_check 으로 돌릴 자격이 여기서 갈린다.
    cut_here = len(rejected) - len(state["rejected_reasons"])
    proposal = _assemble(state, survivors, rejected, cut_here=cut_here)
    return {"scenarios_final": survivors, "rejected_reasons": rejected, "proposal": proposal}


def _assemble(
    state: PurchaseAgentState,
    survivors: list[dict],
    rejected: list[dict],
    *,
    cut_here: int = 0,
) -> dict:
    """제안 JSON을 만들고 출력 경계에서 계약을 재확인한다.

    ``revalidate_for_output()``은 원시 데이터에서 모델을 다시 세워, 조립 과정에서 어긋난
    값이 그대로 나가는 걸 막는다. 여기서 ``ValidationError``가 나면 직렬화하지 않고
    터진다. 계약 위반은 사업적 결과가 아니라 버그이므로 조용히
    빈 제안으로 바꾸지 않는다.
    """
    # 회차를 세는 이름이 슬롯마다 다르다 (#178)::
    #
    #     prior_feedback["condition_seq"]   사람이 조건을 건 회차
    #     feedback_context["attempt"]       매입 재호출 회차   ← ``feedback_attempt`` 는 이것
    #
    #   ``prior_feedback`` 슬롯에는 ``attempt`` 가 없다 (계약 v0.2 §2). 거기서 읽으면
    #   늘 0 이 나온다 — 다른 개념을 같은 이름으로 찾는 셈이다.
    feedback = state["feedback"] or {}
    refeed = state.get("feedback_context") or {}
    raw = {
        "meta": {
            "as_of": state["date"],
            "item": state["item"],
            "agent_version": AGENT_VERSION,
            # 둘 다 되먹임이다. 사람이 조건을 걸어 다시 도는 것과 조언자 판정으로 다시
            # 도는 것은 권위가 다를 뿐 "다시 먹인 실행" 인 것은 같다. ``feedback_context``
            # 만 보면 2회차가 ``False`` 로 나가고, 바로 아래 ``feedback_attempt`` 가 2 인데
            # 재호출이 아니라는 서로를 부정하는 meta 가 된다.
            "is_refeed": bool(feedback) or bool(refeed),
            "feedback_attempt": refeed.get("attempt", 0),
            # 받은 사실을 산출물에 남긴다. 몇 건이 도착했는지는 보내는 쪽이 대조할
            # 수 있어야 한다 — 0 으로만 보이면 "안 보냈다" 와 "보냈는데 못 받았다" 가
            # 같아진다. 마스터 ``flow._adjustment_delivery`` 가 이 값을 읽어 대조한다.
            "received_adjustments": len(state.get("adjustments") or []),
            # 닿았나와 반영했나는 다른 사실이다 (E3-6). 마스터 ``_adjustment_delivery`` 는
            # 위 ``received_adjustments`` 로 «닿았나» 를 대조하고, «반영됐나» 는 이 칸이
            # 알린다.
            #
            #   ``received`` 와 같은 수가 아니다. 항목·단위·대상 안으로 걸러진 것이
            #   빠지고(③ ``split_adjustments``), 그 차이가 곧 "보냈는데 못 썼다" 의
            #   건수다. 둘을 한 칸으로 뭉치면 그 사실이 사라진다.
            "applied_adjustments": len(
                split_adjustments(state.get("adjustments"), load_constraints())[0]
            ),
        },
        "scenarios": with_context_note(survivors, state.get("context_unavailable")),
        "confidence": state["confidence"],
        "situation": state["situation"],
        "context_docs_used": [document_ref(doc["doc_id"]) for doc in state["context_docs"]],
        "rejected_reasons": rejected,
    }
    if not survivors:
        raw["no_proposal_reason"] = no_proposal_reason(rejected, cut_here)
    # 플래그가 끄는 것은 이 한 칸뿐이다 (E3-11 · 착수 조건 ⑤). 같은 판정에서 나온
    # 사람용 문장은 ③이 이미 ``risks`` 에 실었고 플래그와 무관하게 나간다 — 플래그로
    # 고지를 없애면 미결이 조용히 사라지고 규칙 3 이 출력 층에서 깨진다.
    if enabled(INFORMATION_REQUESTS):
        raw["information_requests"] = (state["base_plan"] or {}).get(
            "information_requests"
        ) or []
    # 빈 목록이면 키째로 빠진다 — 그 규칙은 타입에 있다
    # (``PurchaseProposal.drop_empty_information_requests``). 여기서 dump 뒤에 지우면
    #   ``revalidate_for_output`` 의 왕복 항등성이 깨진다.
    return revalidate_for_output(PurchaseProposal.model_validate(raw)).model_dump(
        mode="json"
    )
