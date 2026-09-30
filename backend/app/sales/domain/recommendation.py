"""판매 제안 그래프의 판단 — 추천 확정 · 자기 점검 · 결정 흔적 · 전략 수렴.

★ 2026-09-29 BL-013: `sales/graph.py` 에서 그래프 상태를 읽지 않는 판단을 옮겼다. 그래프
  노드(`service/proposal.py`)는 상태에서 값을 꺼내 여기 넘기고 결과를 상태에 담는다.
  상태 사전을 받던 두 함수(`_resolve_recommendation_id` · `_agent_self_check`)는 필요한 값만
  인자로 받게 바꿨고 판정 순서와 결과는 그대로다.
"""

from __future__ import annotations

from app.sales.domain.proposal import reply_refs
from app.sales.domain.ranking import recommended_scenario_id
from app.sales.schemas.proposal import (
    ProposalSelfCheck,
    SalesDecisionTrace,
    SalesProposalInput,
    SalesScenario,
)
from app.sales.schemas.strategy import StrategyPlan


def resolve_recommendation_id(
    *,
    scenarios: list[SalesScenario],
    ranked_ids: list[str],
    recommendation_id: str | None,
    terminal_reason: str | None,
    rejected: list[SalesScenario],
) -> str | None:
    """**누가 추천인지 확정한다.** 숫자와 규칙만 쓴다 — 모델은 오지 않는다.

    순위를 거쳐 온 길은 이미 첫 자리를 들고 있다. 순위를 건너뛴 길(입력 미비 ·
    검증 대기)은 여기서 처음 추천을 정한다. 그리고 거절된 안이 추천에 앉아 있으면
    추천을 **비운다** — 막힌 안을 권할 수는 없다.
    """
    if recommendation_id is None and ranked_ids:
        recommendation_id = ranked_ids[0]
    if recommendation_id is None and not terminal_reason:
        recommendation_id = recommended_scenario_id(scenarios)
    if any(candidate.scenario_id == recommendation_id for candidate in rejected):
        recommendation_id = None
    return recommendation_id


def recommendation_self_check(
    base_check: ProposalSelfCheck,
    *,
    recommendation_id: str | None,
    rejected: list[SalesScenario],
    validated: list[SalesScenario],
    terminal_reason: str | None,
    feedback_reply_count: int,
) -> ProposalSelfCheck:
    """안 목록 점검(`self_check_scenarios`) 위에 **추천 자체**를 되짚는다."""
    issues = list(base_check.issue_codes)
    rejected_ids = {candidate.scenario_id for candidate in rejected}
    candidates = {candidate.scenario_id: candidate for candidate in validated}
    if not recommendation_id and not terminal_reason:
        issues.append("RECOMMENDATION_MISSING")
    if recommendation_id in rejected_ids:
        issues.append("REJECTED_RECOMMENDATION")
    if recommendation_id and recommendation_id not in candidates:
        issues.append("RECOMMENDATION_NOT_IN_CANDIDATES")
    if recommendation_id and candidates.get(recommendation_id):
        scenario = candidates[recommendation_id]
        if scenario.required_validations and not feedback_reply_count:
            issues.append("RECOMMENDATION_VALIDATION_PENDING")
        if scenario.sales_amount_krw is not None and (
            scenario.quantity_kg is None or scenario.unit_price_krw is None
        ):
            issues.append("RECOMMENDATION_AMOUNT_SOURCE_MISSING")
    issues = unique(issues)
    return base_check.model_copy(
        update={
            "passed": not issues,
            "issue_codes": issues,
            "messages": base_check.messages
            if not issues
            else ["판매안의 추천 후보와 외부 검증 상태를 다시 확인해 주세요."],
        }
    )


def decision_traces(
    *,
    request: SalesProposalInput,
    scenarios: list[SalesScenario],
    candidates: list[SalesScenario],
    rejected: list[SalesScenario],
    ranked_ids: list[str],
    recommendation_id: str | None,
    exclusions: dict[str, list[str]],
) -> list[SalesDecisionTrace]:
    """안마다 **왜 이 자리에 섰는지**를 남긴다. 새로 정하는 것은 없다.

    ```text
    남은 안          순위 · 추천 여부 · 지배당해 빠진 사유(있으면)
    지배당해 빠진 안  빠진 사유
    되먹임이 막은 안  REJECTED_BY_FEEDBACK (앞 두 목록에 없을 때만)
    ```

    ★ 2026-09-29 BL-013: 그래프 최종 조립에서 같은 칸을 세 번 적던 것을 `_trace_facts`
      하나로 모았다. 칸 · 값 · 순서는 그대로다.
    """
    trace = [
        SalesDecisionTrace(
            candidate_id=scenario.scenario_id,
            status=scenario.status,
            rank=ranked_ids.index(scenario.scenario_id) + 1
            if scenario.scenario_id in ranked_ids
            else None,
            recommended=scenario.scenario_id == recommendation_id,
            exclusion_reasons=exclusions.get(scenario.scenario_id, []),
            **_trace_facts(scenario, request),
        )
        for scenario in scenarios
    ]
    trace.extend(
        SalesDecisionTrace(
            candidate_id=scenario.scenario_id,
            status=scenario.status,
            exclusion_reasons=exclusions[scenario.scenario_id],
            **_trace_facts(scenario, request),
        )
        for scenario in candidates
        if scenario.scenario_id in exclusions
    )
    traced_ids = {item.candidate_id for item in trace}
    trace.extend(
        SalesDecisionTrace(
            candidate_id=scenario.scenario_id,
            status=scenario.status,
            exclusion_reasons=["REJECTED_BY_FEEDBACK"],
            **_trace_facts(scenario, request),
        )
        for scenario in rejected
        if scenario.scenario_id not in traced_ids
    )
    return trace


def _trace_facts(scenario: SalesScenario, request: SalesProposalInput) -> dict[str, object]:
    """흔적 세 갈래가 똑같이 옮겨 담는 칸."""
    return {
        "finance_verdict": scenario.finance_verdict,
        "profitability_krw": scenario.contribution_margin_krw,
        "scenario_projected_cash_min": scenario.scenario_projected_cash_min,
        "depends_on_projected_inflow": scenario.depends_on_projected_inflow,
        "inventory_risk_severity": scenario.authoritative_inventory_risk_severity,
        "sell_priority": scenario.sell_priority,
        "remaining_freshness_days": scenario.remaining_freshness_days,
        "dependencies": scenario.execution_dependencies,
        "ml_support_used": scenario.ml_support_used,
        "changed_axes": scenario.sales_decision_axes,
        "unresolved_fields": scenario.uncertainties,
        "reply_refs": reply_refs(scenario.domain_replies),
        "policy_model_refs": (
            [request.ml_context.model_version]
            if scenario.ml_support_used and request.ml_context
            else []
        ),
    }


def strategy_fields(plan: StrategyPlan | None) -> dict[str, object]:
    """전략 출처 세 칸. **계획이 없으면 "꺼져 있었다" 가 아니라 "안 세웠다" 다.**

    ★ 입력이 모자라 전략 노드를 지나지 않은 길에서는 계획 자체가 없다 — 그때는
      `SKIPPED_TEMPLATE` 이다. `DISABLED` 로 적으면 설정을 안 켠 것처럼 읽힌다
      (envelope §LLMStatus 가 가른 바로 그 둘).
    """
    if plan is None:
        return {"strategy_source": "TEMPLATE_FALLBACK", "strategy_llm_status": "SKIPPED_TEMPLATE"}
    return {
        "strategy_source": plan.source,
        "strategy_llm_status": plan.llm_status,
        "strategy_clamped_reason_codes": list(plan.clamped_reason_codes),
        "strategy_llm_failure_reason": plan.llm_failure_reason,
    }


def strategy_collapse(scenarios) -> dict[str, object]:
    """자세는 갈렸는데 **숫자가 수렴했는가.** 숫자를 벌리지 않고 원인만 남긴다.

    ```text
    자세가 한 가지뿐이다          → 수렴이 아니다. 애초에 나뉜 적이 없다
    자세는 여럿인데 단가가 한 가지 → 수렴이다. 무엇이 묶었는지를 적는다
    ```

    🔴 **수렴 원인을 지어내지 않는다.** 코드는 결정론 계산이 실제로 쓴 것
      (`price_strategy_codes`)에서만 온다 — `MARGIN_FLOOR` 가 세 안을 다 묶었으면
      그 이름이 거기 있다.

    ★ **단가가 없는 안은 안 센다.** 가격을 못 만든 것과 같은 값에 닿은 것은 다르다.
    """
    by_price: dict[object, list] = {}
    for scenario in scenarios:
        if scenario.unit_price_krw is not None:
            by_price.setdefault(scenario.unit_price_krw, []).append(scenario)

    reasons: set[str] = set()
    collapsed = False
    for group in by_price.values():
        if len(group) < 2 or len({_posture_of(s) for s in group}) < 2:
            # 같은 자세끼리 같은 값인 것은 수렴이 아니다 — 애초에 안 나뉜 것이다.
            continue
        collapsed = True
        # ★ **그 묶임을 다 설명하는 코드만** 원인이다. 한 안에만 있는 코드는
        #   왜 둘이 같은 값에 닿았는지를 말해 주지 못한다.
        reasons |= set.intersection(*(set(s.price_strategy_codes) for s in group))
    if not collapsed:
        return {}
    return {"strategy_collapsed": True, "strategy_collapse_reason_codes": sorted(reasons)}


def _posture_of(scenario) -> str | None:
    profile = scenario.strategy_profile
    return getattr(profile, "price_posture", None) if profile is not None else None


def is_rejected(candidate) -> bool:
    return candidate.status == "INFEASIBLE" or candidate.finance_verdict == "FAIL"


def validation_reason(validation: str) -> str:
    return {
        "FINANCIAL_VALIDATION": "결제조건·마진·현금 영향은 Finance 검증이 필요합니다.",
        "SELLABLE_SUPPLY_CONTEXT": "판매 가능 수량은 Logistics/Inventory 검증이 필요합니다.",
        "DELIVERY_FEASIBILITY_CONTEXT": "납기 가능 여부는 Logistics 검증이 필요합니다.",
        "ADDITIONAL_SUPPLY_CONTEXT": "부족 물량 확보 가능성은 Purchase 검증이 필요합니다.",
    }.get(validation, "외부 권위 검증이 필요합니다.")


def collapse_reason(reasons: list[str]) -> str | None:
    if len(reasons) == 1:
        return reasons[0]
    if reasons:
        return "SCENARIO_VARIANTS_PARTIALLY_COLLAPSED"
    return None


def unique(values) -> list:
    return list(dict.fromkeys(value for value in values if value))
