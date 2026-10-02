"""SCENARIO_VALIDATION capability — 제출된 시나리오를 검증한다.

이 파일이 소유하는 것
    DataPort 로 읽는 Tool 실행 둘 — 시나리오 투영 overlay · 판정 조립 · 금액 대안 검증

여기 없는 것
    지급 일정 재구성과 정규화 · BASE/STRESS 현금 사건 · 일정 상한(`domain/scenario.py`) ·
    금액 공식(`domain/tools.py`) · BASE/STRESS 판정 규칙(`domain/rules.py`) · 컨텍스트 적재
    (`service/capabilities/procurement.py`) · 실행 통제(`service/harness.py`)

재무는 매입 제안을 고쳐 쓰지 않는다. 제출된 사실을 읽고 투영해 판정할 뿐이고, 조정은 금액
축에서만 제안한다.

금액 대안은 원천이 있는 값만 받는다. 모델이 만든 숫자는 여기 닿지 못한다.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from app.finance.domain import messages
from app.finance.domain.evidence import branch_ref, make_evidence, tool_ref
from app.finance.domain.rules import classify_base_stress
from app.finance.domain.scenario import (
    calculate_schedule_cap,
    payment_row,
    scenario_schedule,
    schedule_events,
)
from app.finance.domain.tools import project_cashflow
from app.finance.schemas.agent_state import FinanceAgentState
from app.finance.schemas.data_port import FinanceAsOfDataPort, FinanceDataNotReady
from app.finance.service.capabilities.procurement import load_context

# ---------------------------------------------------------------------------
# 시나리오 판정과 금액 대안 검증
# ---------------------------------------------------------------------------

def evaluate_purchase_scenario(
data_port: FinanceAsOfDataPort, args: dict[str, Any], state: FinanceAgentState
) -> dict[str, Any]:
    del args
    payload = state.request.payload
    amount = Decimal(str(payload["total_amount_krw"]))
    position, policy, events = load_context(data_port, state)
    horizon = state.request.context.as_of + timedelta(days=policy.cashflow_projection_days)
    schedule = scenario_schedule(
        scenario=payload,
        as_of=state.request.context.as_of,
        horizon=horizon,
        default_payment_days=policy.purchase_payment_days,
    )
    base_projection = project_cashflow(
        as_of=state.request.context.as_of,
        current_cash_krw=Decimal(position["current_cash_krw"]),
        horizon_end=horizon,
        cash_events=events,
    )
    base_scenario_projection = project_cashflow(
        as_of=state.request.context.as_of,
        current_cash_krw=Decimal(position["current_cash_krw"]),
        horizon_end=horizon,
        cash_events=[
            *events,
            *schedule_events(payload["scenario_id"], schedule, stress=False),
        ],
    )
    stress_scenario_projection = project_cashflow(
        as_of=state.request.context.as_of,
        current_cash_krw=Decimal(position["current_cash_krw"]),
        horizon_end=horizon,
        cash_events=[
            *events,
            *schedule_events(payload["scenario_id"], schedule, stress=True),
        ],
    )
    cap = calculate_schedule_cap(
        base_projection=base_projection,
        schedule=schedule,
        total_amount=amount,
        minimum_cash=policy.minimum_cash_balance_krw,
    )
    state.projection = base_projection
    state.scenario_projection = base_scenario_projection
    state.scenario_schedule = schedule
    state.base_state_violated = (
        base_projection.projected_cash_min < policy.minimum_cash_balance_krw
    )
    base_safe = base_scenario_projection.projected_cash_min >= policy.minimum_cash_balance_krw
    stress_safe = (
        stress_scenario_projection.projected_cash_min >= policy.minimum_cash_balance_krw
    )
    scenario_verdict = classify_base_stress(base_safe=base_safe, stress_safe=stress_safe)
    if state.base_state_violated:
        cap = Decimal(0)
        verdict = "reject"
        rule_id = "FIN-BASE-MIN-CASH"
        reason = messages.BASE_MINIMUM_CASH_VIOLATED
    elif scenario_verdict == "ok":
        verdict, rule_id, reason = (
            "ok",
            "FIN-BASE-STRESS",
            messages.SCENARIO_REASON_OK,
        )
    elif scenario_verdict == "conditional":
        verdict, rule_id, reason = (
            "conditional",
            "FIN-BASE-STRESS",
            messages.SCENARIO_REASON_CONDITIONAL,
        )
    else:
        verdict, rule_id, reason = (
            "reject",
            "FIN-BASE-STRESS",
            messages.SCENARIO_REASON_REJECT,
        )
    state.scenario_cap = cap
    scenario_ref = str(payload["scenario_id"])
    return {
        "scenario_id": payload["scenario_id"],
        "verdict": verdict,
        "adjustability": "NOT_NEEDED" if verdict == "ok" else "NOT_ADJUSTABLE",
        "finance_cap_amount_krw": str(cap),
        "scenario_projected_cash_min": str(base_scenario_projection.projected_cash_min),
        "stress_projected_cash_min": str(stress_scenario_projection.projected_cash_min),
        "critical_cash_date": base_scenario_projection.projected_cash_min_date.isoformat(),
        "rule_id": rule_id,
        # 한 배열 안에서 행 모양이 갈리지 않는다. 분할 건과 재구성 건이 서로 다른 키 집합을
        # 내면 읽는 쪽이 매번 어느 모양인지 확인해야 하고, 없는 키를 조용히 놓치기
        # 쉽다.
        #
        # 이것은 재무 검증 메타데이터다. 고쳐 쓴 매입 제안이 아니다.
        "payment_schedule": [payment_row(item) for item in schedule],
        "reason": reason,
        "rules": [{"rule_id": rule_id, "status": "PASS" if verdict == "ok" else "FAIL"}],
        "evidence": [
            # 주의: `1` 은 시나리오 id 가 아니다. 실제 식별자는 `ref_ids` 에 있다.
            #
            # 공용 `Evidence.value` 가 `float` 필수라, 문자열 식별자를 실을 자리가 없어서
            # "이 시나리오가 있다"는 존재 표시로 1 을 넣는다. 값을 시나리오 번호로 읽으면
            # 안 된다.
            #
            # 제대로 고치려면 공용 계약(`app.contracts.core.Evidence`)이 비수치 식별을
            # 허용해야 한다 — 재무 밖이라 여기서 바꾸지 않는다(CROSS-DOMAIN). 현재 이 값을
            # 읽는 소비자는 없다(Critic 의 재무 claim 목록에도 없다).
            make_evidence("scenario_id", 1, "identity", scenario_ref),
            make_evidence(
                "finance_cap_amount_krw",
                cap,
                "krw",
                tool_ref("evaluate_purchase_scenario", state),
            ),
            make_evidence(
                "scenario_projected_cash_min",
                base_scenario_projection.projected_cash_min,
                "krw",
                branch_ref("cashflow", state),
            ),
            make_evidence(
                "stress_projected_cash_min",
                stress_scenario_projection.projected_cash_min,
                "krw",
                branch_ref("stress-cashflow", state),
            ),
            make_evidence("verdict", verdict == "ok", "boolean", branch_ref(rule_id, state)),
            make_evidence(
                "payment_schedule",
                len(schedule),
                "payment_count",
                branch_ref("payment-schedule", state),
            ),
            make_evidence(
                "adjustability",
                0 if verdict == "ok" else 2,
                "enum_code",
                branch_ref(rule_id, state),
            ),
        ],
    }
def validate_amount_adjustment(
data_port: FinanceAsOfDataPort, args: dict[str, Any], state: FinanceAgentState
) -> dict[str, Any]:
    axis = args.get("axis", "amount")
    if axis != "amount":
        raise ValueError("Finance may adjust only the amount axis")
    candidate = Decimal(str(args["candidate_amount_krw"]))
    if candidate < 0:
        raise ValueError("candidate amount must not be negative")
    load_context(data_port, state)
    cap = state.scenario_cap
    if cap is None:
        raise FinanceDataNotReady("scenario_finance_cap")
    source_values = {
        Decimal(str(state.request.payload[key]))
        for key in ("candidate_amount_krw", "proposed_amount_krw")
        if state.request.payload.get(key) is not None
    }
    source_values.add(cap)
    if candidate not in source_values:
        raise ValueError("candidate amount has no DB, policy, payload, or Tool evidence source")
    valid = candidate <= cap
    return {
        "candidate_amount_krw": str(candidate),
        "validation_status": "PASS" if valid else "FAIL",
        "evidence": [
            make_evidence(
                "candidate_amount_krw",
                candidate,
                "krw",
                tool_ref("validate_amount_adjustment", state),
            ),
            make_evidence(
                "validation_status",
                valid,
                "boolean",
                branch_ref("FIN-CAP", state),
            ),
        ],
    }
