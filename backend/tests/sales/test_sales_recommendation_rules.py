"""판매 제안 그래프의 판단 — 추천 확정 · 추천 자기 점검 · 결정 흔적 · 전략 출처.

★ 2026-09-29 BL-013: 이 판단들은 `sales/graph.py` 안에서 그래프 상태 사전을 읽으며 돌았다.
  `domain/recommendation.py` 로 옮기면서 필요한 값만 인자로 받게 했고, 그래프 노드
  (`service/proposal.py`)는 상태에서 값을 꺼내 넘긴다. 그래프를 통째로 도는 검사
  (`test_sales_validation_second_pass.py` 등)와 별개로, 판정 하나하나를 입력 → 출력으로 잰다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.sales.domain.recommendation import (
    decision_traces,
    recommendation_self_check,
    resolve_recommendation_id,
    strategy_fields,
)
from app.sales.schemas.proposal import ProposalSelfCheck, SalesProposalInput, SalesScenario
from app.sales.schemas.strategy import StrategyPlan


def _scenario(scenario_id: str, **over: Any) -> SalesScenario:
    data: dict[str, Any] = {
        "scenario_id": scenario_id,
        "scenario_type": "CONSERVATIVE",
        "objective": "RISK_DEFENSE",
        "business_mode": "SPOT_SALES",
        "item": "배추",
        "quantity_kg": "100",
        "unit_price_krw": "1500",
        "sales_amount_krw": "150000",
        "supply": {"confirmed_quantity_kg": "100"},
        "status": "EXECUTABLE",
        "finance_verdict": "PASS",
        "contribution_margin_krw": "30000",
    }
    data.update(over)
    return SalesScenario.model_validate(data)


def _request(**over: Any) -> SalesProposalInput:
    return SalesProposalInput.model_validate(
        {"business_mode": "SPOT_SALES", "user_request": {"item": "배추"}, **over}
    )


def test_the_first_ranked_candidate_is_the_recommendation():
    a, b = _scenario("A"), _scenario("B")

    assert (
        resolve_recommendation_id(
            scenarios=[a, b],
            ranked_ids=["B", "A"],
            recommendation_id=None,
            terminal_reason=None,
            rejected=[],
        )
        == "B"
    )


def test_an_already_settled_recommendation_is_kept():
    assert (
        resolve_recommendation_id(
            scenarios=[_scenario("A")],
            ranked_ids=["A"],
            recommendation_id="X",
            terminal_reason=None,
            rejected=[],
        )
        == "X"
    )


def test_a_path_that_skipped_ranking_ranks_now_unless_it_ended_early():
    scenarios = [_scenario("A", finance_verdict="REVIEW_REQUIRED"), _scenario("B")]

    assert (
        resolve_recommendation_id(
            scenarios=scenarios,
            ranked_ids=[],
            recommendation_id=None,
            terminal_reason=None,
            rejected=[],
        )
        == "B"
    )
    #  🔴 입력 미비 · 검증 대기로 끝난 길은 추천을 지어내지 않는다.
    assert (
        resolve_recommendation_id(
            scenarios=scenarios,
            ranked_ids=[],
            recommendation_id=None,
            terminal_reason="VALIDATION_REQUIRED",
            rejected=[],
        )
        is None
    )


def test_a_rejected_candidate_is_never_recommended():
    rejected = _scenario("A", finance_verdict="FAIL")

    assert (
        resolve_recommendation_id(
            scenarios=[rejected],
            ranked_ids=["A"],
            recommendation_id=None,
            terminal_reason=None,
            rejected=[rejected],
        )
        is None
    )


def _check(**over: Any) -> ProposalSelfCheck:
    kwargs: dict[str, Any] = {
        "recommendation_id": "A",
        "rejected": [],
        "validated": [_scenario("A")],
        "terminal_reason": None,
        "feedback_reply_count": 1,
    }
    kwargs.update(over)
    return recommendation_self_check(ProposalSelfCheck(passed=True), **kwargs)


def test_a_clean_recommendation_passes_and_keeps_the_messages():
    check = recommendation_self_check(
        ProposalSelfCheck(passed=True, messages=["좋다"]),
        recommendation_id="A",
        rejected=[],
        validated=[_scenario("A")],
        terminal_reason=None,
        feedback_reply_count=1,
    )

    assert check.passed is True
    assert check.issue_codes == []
    assert check.messages == ["좋다"]


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"recommendation_id": None}, "RECOMMENDATION_MISSING"),
        ({"rejected": [_scenario("A")]}, "REJECTED_RECOMMENDATION"),
        ({"validated": [_scenario("B")]}, "RECOMMENDATION_NOT_IN_CANDIDATES"),
        (
            {
                "validated": [_scenario("A", required_validations=["FINANCIAL_VALIDATION"])],
                "feedback_reply_count": 0,
            },
            "RECOMMENDATION_VALIDATION_PENDING",
        ),
        (
            {"validated": [_scenario("A", unit_price_krw=None)]},
            "RECOMMENDATION_AMOUNT_SOURCE_MISSING",
        ),
    ],
)
def test_each_recommendation_problem_is_named(over, code):
    check = _check(**over)

    assert check.passed is False
    assert code in check.issue_codes
    assert check.messages == ["판매안의 추천 후보와 외부 검증 상태를 다시 확인해 주세요."]


def test_a_missing_recommendation_on_a_path_that_ended_early_is_not_a_problem():
    assert _check(recommendation_id=None, terminal_reason="INPUT_INCOMPLETE").passed is True


def test_a_pending_validation_is_not_a_problem_once_replies_arrived():
    validated = [_scenario("A", required_validations=["FINANCIAL_VALIDATION"])]

    assert _check(validated=validated, feedback_reply_count=2).passed is True


def test_the_base_check_issues_are_kept_ahead_of_the_recommendation_issues():
    check = recommendation_self_check(
        ProposalSelfCheck(passed=False, issue_codes=["SCENARIO_ID_DUPLICATE"]),
        recommendation_id=None,
        rejected=[],
        validated=[],
        terminal_reason=None,
        feedback_reply_count=0,
    )

    assert check.issue_codes == ["SCENARIO_ID_DUPLICATE", "RECOMMENDATION_MISSING"]


def test_every_candidate_leaves_a_trace_with_why_it_stands_there():
    kept = _scenario("A")
    dominated = _scenario("B", contribution_margin_krw=Decimal(1))
    rejected = _scenario("C", finance_verdict="FAIL")

    trace = decision_traces(
        request=_request(),
        scenarios=[kept],
        candidates=[kept, dominated, rejected],
        rejected=[rejected, dominated],
        ranked_ids=["A"],
        recommendation_id="A",
        exclusions={"B": ["DOMINATED_BY:A"]},
    )

    assert [(t.candidate_id, t.rank, t.recommended, t.exclusion_reasons) for t in trace] == [
        ("A", 1, True, []),
        ("B", None, False, ["DOMINATED_BY:A"]),
        #  되먹임이 막은 안은 앞 두 목록에 없을 때만 한 번 적힌다.
        ("C", None, False, ["REJECTED_BY_FEEDBACK"]),
    ]
    assert trace[0].profitability_krw == Decimal(30000)
    assert trace[2].finance_verdict == "FAIL"


def test_a_path_without_a_plan_says_the_strategy_was_not_built():
    """🔴 `DISABLED` 가 아니다 — 설정을 안 켠 것과 계획을 안 세운 것은 다르다."""
    assert strategy_fields(None) == {
        "strategy_source": "TEMPLATE_FALLBACK",
        "strategy_llm_status": "SKIPPED_TEMPLATE",
    }


def test_a_plan_carries_its_source_and_failure_reason():
    plan = StrategyPlan(
        source="TEMPLATE_FALLBACK",
        llm_status="FALLBACK",
        profiles=[],
        clamped_reason_codes=["AGGRESSIVE:DEPLETION_SIGNAL_ABSENT"],
        llm_failure_reason="HTTP_429",
    )

    assert strategy_fields(plan) == {
        "strategy_source": "TEMPLATE_FALLBACK",
        "strategy_llm_status": "FALLBACK",
        "strategy_clamped_reason_codes": ["AGGRESSIVE:DEPLETION_SIGNAL_ABSENT"],
        "strategy_llm_failure_reason": "HTTP_429",
    }
