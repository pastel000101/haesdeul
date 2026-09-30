"""LangGraph 기반 Sales proposal agent.

★ 2026-09-29 BL-013: `sales/graph.py`(그래프)와 `sales/proposal.py::run_proposal`(입구)을 합쳐
  옮겼다. 노드는 상태를 읽고 쓰기만 하고, 판단은 `domain/`(안 생성 · 순위 · 추천 · 자기 점검 ·
  결정 흔적), 전략 계획은 `service/strategy.py`, 해석 모델은 `llm/runtime.py` 다. 판매 후보를
  이력에 남기는 실행은 이 그래프를 부르는 `service/proposal_generation.py` 다.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.sales.domain.proposal import (
    all_feedback_replies,
    generate_scenarios,
    interpretation_candidates,
    missing_capabilities,
    self_check_scenarios,
    validate_context,
)
from app.sales.domain.ranking import rank_scenarios, remove_dominated_scenarios
from app.sales.domain.recommendation import (
    collapse_reason,
    decision_traces,
    is_rejected,
    recommendation_self_check,
    resolve_recommendation_id,
    strategy_collapse,
    strategy_fields,
    unique,
    validation_reason,
)
from app.sales.llm.runtime import interpret_candidates
from app.sales.schemas.proposal import SalesProposalInput, SalesProposalReply
from app.sales.schemas.proposal_state import SalesAgentState
from app.sales.service.strategy import plan_strategies


def run_proposal(request: SalesProposalInput) -> SalesProposalReply:
    """판매 제안 그래프를 한 번 돌려 회신을 만든다. **이력을 쓰지 않는다.**

    ★ 2026-09-29 BL-013: 전에는 `proposal.run_proposal` 이 그래프 모듈을 함수 안에서 불러
      `run_sales_agent` 에 넘기기만 했다(`proposal ⇄ graph` 순환을 피하려던 지연 import).
      그래프가 판단을 `domain/` 에서 가져오게 되어 순환이 없어졌고, 입구를 하나로 합쳤다.
    """
    state = _graph().invoke({"request": request})
    return state["reply"]


def _graph():
    graph = StateGraph(SalesAgentState)
    graph.add_node("prepare_context", _prepare_context)
    graph.add_node("classify_situation", _classify_situation)
    graph.add_node("plan_strategy", _plan_strategy)
    graph.add_node("generate_candidates", _generate_candidates)
    graph.add_node("plan_validations", _plan_validations)
    graph.add_node("apply_feedback", _apply_feedback)
    graph.add_node("evaluate_candidates", _evaluate_candidates)
    graph.add_node("rank_candidates", _rank_candidates)
    graph.add_node("self_check", _self_check)
    graph.add_node("interpret_recommendation", _interpret_recommendation)
    graph.add_node("final_recommendation", _final_recommendation)

    graph.add_edge(START, "prepare_context")
    graph.add_edge("prepare_context", "classify_situation")
    graph.add_conditional_edges(
        "classify_situation",
        _route_after_situation,
        {"incomplete": "self_check", "generate": "plan_strategy"},
    )
    # ★ 입력이 모자란 길에서는 전략을 세우지 않는다 — 만들 안이 없는데 모델을 부르면
    #   그 호출은 아무것도 바꾸지 못하고 비용만 쓴다.
    graph.add_edge("plan_strategy", "generate_candidates")
    graph.add_edge("generate_candidates", "plan_validations")
    graph.add_conditional_edges(
        "plan_validations",
        _route_after_validation_plan,
        {
            "feedback": "apply_feedback",
            "evaluate": "evaluate_candidates",
            "self_check": "self_check",
        },
    )
    graph.add_edge("apply_feedback", "evaluate_candidates")
    graph.add_edge("evaluate_candidates", "rank_candidates")
    graph.add_edge("rank_candidates", "self_check")
    graph.add_conditional_edges(
        "self_check",
        _route_after_self_check,
        {"rerank": "rank_candidates", "final": "interpret_recommendation"},
    )
    graph.add_edge("interpret_recommendation", "final_recommendation")
    graph.add_edge("final_recommendation", END)
    return graph.compile()


def _prepare_context(state: SalesAgentState) -> SalesAgentState:
    request = state["request"]
    feedback = request.feedback
    agent_trace = [
        {
            "stage": "prepare_context",
            "business_mode": request.business_mode,
            "feedback_attempt": request.feedback_attempt,
            "has_feedback": bool(feedback and feedback.domain_replies),
        }
    ]
    return {
        **state,
        "business_mode": request.business_mode,
        "feedback_attempt": request.feedback_attempt,
        "external_feedback": {
            "reply_count": len(feedback.domain_replies) if feedback else 0,
            "scenario_feedback_count": len(feedback.scenario_feedback) if feedback else 0,
        },
        "sales_context": {
            "has_contract": request.contract_context is not None,
            "has_logistics": request.logistics_context is not None,
            "has_finance": request.finance_context is not None,
            "is_refeed": request.is_refeed,
        },
        "agent_trace": agent_trace,
    }


def _classify_situation(state: SalesAgentState) -> SalesAgentState:
    missing = validate_context(state["request"])
    return {
        **state,
        "missing_data": missing,
        "status": "INPUT_INCOMPLETE" if missing else "READY_TO_GENERATE",
        "terminal_reason": "INPUT_INCOMPLETE" if missing else None,
        "agent_trace": [
            *state.get("agent_trace", []),
            {"stage": "classify_situation", "missing_data": missing},
        ],
    }


def _route_after_situation(state: SalesAgentState) -> str:
    return "incomplete" if state.get("missing_data") else "generate"


def _plan_strategy(state: SalesAgentState) -> SalesAgentState:
    """**후보를 만들기 전에 세 전략의 자세를 정한다** (2026-09-16).

    🔴 **모델이 불리는 두 번째 자리이고, 앞자리다.** 뒤쪽 `interpret_recommendation`
      은 이미 정해진 추천을 말로 옮기는 자리라 전략에 참여하지 않는다 — 그래서
      판매안이 *"모델이 만든 전략"* 인 적이 없었다.

    🔴 **여기서도 숫자는 안 나온다.** 모델은 자세(닫힌 어휘)만 고르고, 그 자세가
      실제 단가·수량이 되는 것은 `_generate_scenarios` 의 결정론 계산이다.

    ★ **계획은 한 실행에 한 번 선다.** 노드를 따로 세운 이유가 이것이다 — 후보
      생성 안에서 매번 만들면 모델을 여러 번 부르고, 회차마다 다른 자세가 나오면
      같은 실행 안에서 세 안의 기준이 갈린다.
    """
    request = state["request"]
    plan, signals = plan_strategies(request, all_feedback_replies(request))
    return {
        **state,
        "strategy_plan": plan,
        "agent_trace": [
            *state.get("agent_trace", []),
            {
                "stage": "plan_strategy",
                "strategy_source": plan.source,
                "llm_status": plan.llm_status,
                "depletion_pressure": signals.depletion_pressure,
                "postures": [
                    {"strategy": p.strategy, "price_posture": p.price_posture}
                    for p in plan.profiles
                ],
                "clamped": plan.clamped_reason_codes,
            },
        ],
    }


def _generate_candidates(state: SalesAgentState) -> SalesAgentState:
    #  ★ 계획은 바로 앞 `plan_strategy` 노드가 늘 세운다 — 이 노드로 오는 길은 그것 하나다.
    candidates = generate_scenarios(state["request"], state["strategy_plan"])
    terminal_reason = None if candidates else "NO_SALES_CANDIDATE"
    return {
        **state,
        "candidates": candidates,
        "status": "CANDIDATES_GENERATED" if candidates else "NO_OPPORTUNITY",
        "terminal_reason": terminal_reason,
        "agent_trace": [
            *state.get("agent_trace", []),
            {"stage": "generate_candidates", "candidate_count": len(candidates)},
        ],
    }


def _plan_validations(state: SalesAgentState) -> SalesAgentState:
    required: list[dict[str, str]] = []
    for candidate in state.get("candidates", []):
        for validation in candidate.required_validations:
            entry = {
                "candidate_id": candidate.scenario_id,
                "validation": validation,
                "reason": validation_reason(validation),
            }
            if entry not in required:
                required.append(entry)
    return {
        **state,
        "required_validations": required,
        "terminal_reason": "VALIDATION_REQUIRED" if required else state.get("terminal_reason"),
        "agent_trace": [
            *state.get("agent_trace", []),
            {"stage": "determine_validations", "required_validations": required},
        ],
    }


def _route_after_validation_plan(state: SalesAgentState) -> str:
    if not state.get("candidates"):
        return "self_check"
    if state.get("required_validations") and not state.get("external_feedback", {}).get(
        "reply_count", 0
    ):
        return "self_check"
    feedback = state.get("external_feedback", {})
    return "feedback" if feedback.get("reply_count", 0) else "evaluate"


def _apply_feedback(state: SalesAgentState) -> SalesAgentState:
    candidates = state.get("candidates", [])
    rejected = [candidate for candidate in candidates if is_rejected(candidate)]
    return {
        **state,
        "candidates": candidates,
        "rejected_candidates": rejected,
        "status": "FEEDBACK_APPLIED",
        "terminal_reason": None,
        "agent_trace": [
            *state.get("agent_trace", []),
            {
                "stage": "apply_feedback",
                "rejected_candidate_ids": [candidate.scenario_id for candidate in rejected],
                "conditional_candidate_ids": [
                    candidate.scenario_id
                    for candidate in candidates
                    if candidate.status == "CONDITIONAL"
                ],
            },
        ],
    }


def _evaluate_candidates(state: SalesAgentState) -> SalesAgentState:
    candidates, excluded = remove_dominated_scenarios(state.get("candidates", []))
    candidates = [candidate for candidate in candidates if not is_rejected(candidate)]
    rejected = [
        candidate
        for candidate in state.get("candidates", [])
        if is_rejected(candidate) or candidate.scenario_id in excluded
    ]
    return {
        **state,
        "validated_candidates": candidates,
        "rejected_candidates": rejected,
        "excluded_reasons": excluded,
        "agent_trace": [
            *state.get("agent_trace", []),
            {
                "stage": "evaluate_candidates",
                "selectable_count": len(
                    [candidate for candidate in candidates if candidate.status == "EXECUTABLE"]
                ),
                "conditional_count": len(
                    [candidate for candidate in candidates if candidate.status == "CONDITIONAL"]
                ),
                "rejected_candidate_ids": [candidate.scenario_id for candidate in rejected],
            },
        ],
    }


def _rank_candidates(state: SalesAgentState) -> SalesAgentState:
    ranked = rank_scenarios(state.get("validated_candidates", []))
    ranked_ids = [candidate.scenario_id for candidate in ranked]
    recommendation_id = ranked_ids[0] if ranked_ids else None
    return {
        **state,
        "ranked_candidate_ids": ranked_ids,
        "recommendation_id": recommendation_id,
        "agent_trace": [
            *state.get("agent_trace", []),
            {"stage": "rank_candidates", "ranked_candidate_ids": ranked_ids},
        ],
    }


def _self_check(state: SalesAgentState) -> SalesAgentState:
    """**결정한 것을 스스로 되짚는다.** 여기를 지나면 추천이 확정된다.

    설명하는 node 가 뒤에 따로 서 있으므로 그 앞에서 추천이 굳어 있어야 한다.
    그래서 최종 조립이 아니라 이 자리에서 `_resolve_recommendation_id` 를 부른다.
    다시 순위를 매기러 돌아가는 길에서는 부르지 않는다 — 곧 순위가 다시 정할 값이다.
    """
    scenarios = state.get("validated_candidates", [])
    check = recommendation_self_check(
        self_check_scenarios(scenarios),
        recommendation_id=state.get("recommendation_id"),
        rejected=state.get("rejected_candidates", []),
        validated=state.get("validated_candidates", []),
        terminal_reason=state.get("terminal_reason"),
        feedback_reply_count=state.get("external_feedback", {}).get("reply_count", 0),
    )
    if check.passed or state.get("feedback_attempt", 0) >= 1:
        settled = {
            **state,
            "self_check": check,
            "agent_trace": [
                *state.get("agent_trace", []),
                {"stage": "self_check", "passed": check.passed, "issues": check.issue_codes},
            ],
        }
        return {**settled, "recommendation_id": _resolve_recommendation_id(settled)}
    filtered = [scenario for scenario in scenarios if not is_rejected(scenario)]
    if len(filtered) != len(scenarios):
        return {
            **state,
            "validated_candidates": filtered,
            "self_check": check,
            "self_check_reranked": True,
            "agent_trace": [
                *state.get("agent_trace", []),
                {"stage": "self_check", "passed": False, "action": "RERANK_WITHOUT_REJECTED"},
            ],
        }
    cleared = {
        **state,
        "recommendation_id": None,
        "self_check": check,
        "self_check_reranked": True,
        "agent_trace": [
            *state.get("agent_trace", []),
            {"stage": "self_check", "passed": False, "action": "CLEAR_RECOMMENDATION"},
        ],
    }
    return {**cleared, "recommendation_id": _resolve_recommendation_id(cleared)}


def _resolve_recommendation_id(state: SalesAgentState) -> str | None:
    """**누가 추천인지 확정한다.** 숫자와 규칙만 쓴다 — 모델은 오지 않는다.

    순위를 거쳐 온 길은 이미 첫 자리를 들고 있다. 순위를 건너뛴 길(입력 미비 ·
    검증 대기)은 여기서 처음 추천을 정한다. 그리고 거절된 안이 추천에 앉아 있으면
    추천을 **비운다** — 막힌 안을 권할 수는 없다. 판정은 `domain.resolve_recommendation_id`
    이고, 여기서는 상태에서 값을 꺼내 넘긴다.
    """
    return resolve_recommendation_id(
        scenarios=state.get("validated_candidates", state.get("candidates", [])),
        ranked_ids=state.get("ranked_candidate_ids", []),
        recommendation_id=state.get("recommendation_id"),
        terminal_reason=state.get("terminal_reason"),
        rejected=state.get("rejected_candidates", []),
    )


def _route_after_self_check(state: SalesAgentState) -> str:
    check = state.get("self_check")
    if (
        check
        and not check.passed
        and state.get("feedback_attempt", 0) < 1
        and not state.get("self_check_reranked")
    ):
        return "rerank"
    return "final"


def _interpret_recommendation(state: SalesAgentState) -> SalesAgentState:
    """**정해진 추천을 말로 옮긴다.** 그래프에서 모델이 불리는 유일한 자리다.

    이 node 는 아무것도 고르지 않는다. 추천은 `rank_candidates` 가 정하고
    `self_check` 가 확정한 뒤 여기 도착한다. 수량·단가·금액·날짜·결제조건은
    읽지도 넘기지도 않는다 — 모델에는 라벨만 간다.

    ★ 검증이 끝나지 않은 안(`UNRESOLVED`)과 막힌 안(`INFEASIBLE`)은
      `_interpret_scenarios` 가 애초에 후보에서 뺀다. 그래서 첫 실행에서는 설명할
      것이 없어 `SKIPPED_TEMPLATE` 로 끝나고 모델을 부르지 않는다. **node 를 따로
      세웠다는 이유로 판정 전 안을 설명하게 두면, 보지 않은 안이 제안처럼 읽힌다.**
    """
    scenarios = state.get("validated_candidates", state.get("candidates", []))
    recommendation_id = state.get("recommendation_id")
    recommendation = interpret_candidates(
        interpretation_candidates(scenarios), recommended_candidate_id=recommendation_id
    )
    return {
        **state,
        "recommendation": recommendation,
        "agent_trace": [
            *state.get("agent_trace", []),
            {
                "stage": "interpret_recommendation",
                "recommended_scenario_id": recommendation_id,
                "llm_status": recommendation.status,
                "llm_attempts": recommendation.llm_attempts,
            },
        ],
    }


def _final_recommendation(state: SalesAgentState) -> SalesAgentState:
    """**답장을 조립한다.** 새로 정하는 것은 없다 — 앞에서 정해진 것을 옮겨 담는다."""
    request = state["request"]
    scenarios = state.get("validated_candidates", state.get("candidates", []))
    ranked_ids = state.get("ranked_candidate_ids", [])
    recommendation_id = state.get("recommendation_id")
    recommendation = state["recommendation"]
    exclusions = state.get("excluded_reasons", {})
    trace = decision_traces(
        request=request,
        scenarios=scenarios,
        candidates=state.get("candidates", []),
        rejected=state.get("rejected_candidates", []),
        ranked_ids=ranked_ids,
        recommendation_id=recommendation_id,
        exclusions=exclusions,
    )
    collapse_reasons = unique(
        scenario.variant_collapsed_reason
        for scenario in scenarios
        if scenario.variant_collapsed_reason
    )
    reply = SalesProposalReply(
        status="INPUT_INCOMPLETE" if state.get("missing_data") else "SCENARIOS_GENERATED",
        business_mode=request.business_mode,
        is_refeed=request.is_refeed,
        feedback_attempt=request.feedback_attempt,
        scenarios=scenarios,
        variant_collapsed=bool(collapse_reasons),
        variant_collapsed_reason=collapse_reason(collapse_reasons),
        missing_data=state.get("missing_data", []),
        missing_capabilities=missing_capabilities(request),
        recommended_scenario_id=recommendation_id,
        llm=recommendation,
        recommendation=recommendation,
        self_check=state["self_check"],
        decision_trace=trace,
        # 🔴 **장애를 숨기지 않는다** (§10). 모델이 실패했는데 성공처럼 보이면 모델이
        #   죽은 날과 산 날이 화면에서 같아진다.
        **strategy_fields(state.get("strategy_plan")),
        # ★ 제약 때문에 숫자가 수렴한 사실은 **버그가 아니라 결과의 일부**다 (§8).
        **strategy_collapse(scenarios),
    )
    return {
        **state,
        "decision_trace": trace,
        "recommendation": recommendation,
        "recommendation_id": recommendation_id,
        "agent_trace": [
            *state.get("agent_trace", []),
            {
                "stage": "final_recommendation",
                "terminal_reason": state.get("terminal_reason"),
                "recommended_scenario_id": recommendation_id,
                "self_check_passed": state["self_check"].passed,
            },
        ],
        "reply": reply,
    }
