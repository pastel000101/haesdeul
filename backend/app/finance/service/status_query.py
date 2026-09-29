"""STATUS_QUERY — «지금 자금 상황» 조회.

★ 2026-09-29 재구성 BL-014: `finance/adapter.py` 에서 옮겼다(몸통 그대로). 계산 도우미는
  `domain/status_facts.py`,
  회신 · 이력 도우미는 `service/agent_replies.py`.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.contracts.envelope import AgentReply, AgentRequest, ExecutionMetadata
from app.finance.domain import messages
from app.finance.domain.evidence import PAYROLL_SOURCE_KEYS
from app.finance.domain.status_facts import (
    JUDGMENT_FIELDS,
    T_CASHFLOW,
    T_POSITION,
    T_PRESSURE,
    as_float,
    evidence_item,
    find_critical_payment_dates,
    outflows_by_date,
    policy_ref,
    safe_ratio,
)
from app.finance.domain.tools import (
    build_payroll_schedule,
    calculate_projected_cash_min,
    derive_cash_priority,
    project_cashflow,
)
from app.finance.schemas.agent_state import missing_source_name
from app.finance.service.agent_replies import (
    adapter_metadata,
    axis_not_ready_reply,
    new_run_id,
    not_ready_reply,
)
from app.finance.service.agent_run import load_runtime_context

# ---------------------------------------------------------------------------
# STATUS_QUERY — "지금 자금 상황" 조회
# ---------------------------------------------------------------------------


def answer_status_query(request: AgentRequest) -> tuple[AgentReply, ExecutionMetadata]:
    """묻기만 하는 요청. **경계가 아니라 상태**를 돌려준다.

    ★ `PRE_PURCHASE` 와 계산은 같고 **싣는 것이 다르다.** `finance_cap` ·
      `purchase_payment_days` 같은 값은 *매입 판단을 위한 경계*라 "지금 자금 상황"
      을 묻는 사람에게는 답이 아니다.

    ★ **급여 출처가 없어도 답을 낸다 — 다만 낼 수 있는 것만.**
      `PRE_PURCHASE` 는 급여 출처가 없으면 통째로 멈춘다. 급여 유출이 빠진 투영으로
      만든 `finance_cap` 은 **낙관적으로 틀리고**, 그 상한으로 매입이 실행되기 때문이다.
      조회는 실행으로 이어지지 않으므로 **현재 현금처럼 투영이 필요 없는 값은 답하고**,
      투영이 필요한 값만 빼고 이름을 밝힌다 (§3.7.6 — 못 한 것을 한 척하지 않는다).
    """
    as_of = request.context.as_of
    run_id = new_run_id(request)
    tools: list[str] = [T_POSITION]

    sim_run_id = request.context.sim_run_id
    if not sim_run_id.strip():
        return axis_not_ready_reply(request, run_id)

    context = load_runtime_context(as_of, sim_run_id=sim_run_id)
    if context is None:
        return not_ready_reply(
            request,
            run_id,
            tools,
            missing=("finance_state", "finance_policy"),
            reason=messages.CONTEXT_UNAVAILABLE,
        )

    state_date = context.snapshot.state_date
    if state_date != as_of:
        return not_ready_reply(
            request,
            run_id,
            tools,
            missing=(f"finance_state@{as_of.isoformat()}",),
            reason=messages.AS_OF_MISMATCH,
        )

    policy = context.policy
    ref = context.snapshot.finance_state_id
    missing: list[str] = []
    payload: dict[str, Any] = {
        "as_of": as_of.isoformat(),
        "state_date": state_date.isoformat(),
        "available_cash": as_float(context.snapshot.current_cash_krw),
        "policy_version_used": policy.policy_version,
    }
    evidences = (
        evidence_item(
            "available_cash", context.snapshot.current_cash_krw, "KRW", ref, "재무 상태 현재 잔액"
        ),
    )

    # 🔴 값이 Policy 에서 왔으면 근거도 Policy 를 가리켜야 한다. 출처가 없으면
    #    스냅샷 id 로 때우지 않고 **값과 근거를 함께 뺀다** (`policy_ref` 참조).
    minimum_cash_ref = policy_ref(policy, "minimum_cash_balance_krw", missing)
    if minimum_cash_ref is not None:
        payload["minimum_cash_balance_krw"] = as_float(policy.minimum_cash_balance_krw)
        evidences = (
            *evidences,
            evidence_item(
                "minimum_cash_balance_krw",
                policy.minimum_cash_balance_krw,
                "KRW",
                minimum_cash_ref,
                f"Finance Policy {policy.policy_version} · 1개월 급여 Reserve",
                grade="SIM_FIXED",
            ),
        )

    payroll_refs = tuple(
        missing_source_name(key)
        for key in PAYROLL_SOURCE_KEYS
        if not policy.source_refs.get(key)
    )
    if payroll_refs:
        # 투영이 필요한 값만 뺀다. 현재 잔액은 그대로 답한다.
        missing.extend(payroll_refs)
    else:
        tools.extend((T_CASHFLOW, T_PRESSURE))
        horizon_end = as_of + timedelta(days=policy.cashflow_projection_days)
        events = (
            *context.cash_events,
            *build_payroll_schedule(as_of=as_of, horizon_end=horizon_end, policy=policy),
        )
        projection = project_cashflow(
            as_of=as_of,
            current_cash_krw=context.snapshot.current_cash_krw,
            horizon_end=horizon_end,
            cash_events=events,
        )
        outflows = outflows_by_date([e for e in events if as_of < e.event_date <= horizon_end])
        cash_min = calculate_projected_cash_min(projection)
        pressure = derive_cash_priority(projected_cash_min=cash_min, policy=policy)
        payload["projected_cash_min"] = as_float(cash_min)
        payload["payment_pressure"] = pressure
        evidences = (
            *evidences,
            evidence_item(
                "projected_cash_min",
                cash_min,
                "KRW",
                ref,
                f"D+{policy.cashflow_projection_days} 투영 최저",
            ),
            evidence_item(
                "payment_pressure",
                safe_ratio(cash_min, policy.minimum_cash_balance_krw),
                "ratio",
                ref,
                f"투영최저/최소현금 = 임계 {policy.cash_priority_high_ratio}"
                f"/{policy.cash_priority_medium_ratio} → {pressure}",
            ),
        )

        projection_days_ref = policy_ref(policy, "cashflow_projection_days", missing)
        if projection_days_ref is not None:
            payload["projection_days"] = policy.cashflow_projection_days
            evidences = (
                *evidences,
                evidence_item(
                    "projection_days",
                    policy.cashflow_projection_days,
                    "day",
                    projection_days_ref,
                    "현금 투영 Horizon — Finance Policy 값",
                    grade="SIM_FIXED",
                ),
            )

        if minimum_cash_ref is not None:
            critical_dates = find_critical_payment_dates(
                projection, outflows, policy.minimum_cash_balance_krw
            )
            payload["critical_payment_dates"] = critical_dates
            evidences = (
                *evidences,
                evidence_item(
                    # ★ 목록의 **개수**가 아니라 그 목록을 만든 **임계값**을 넣는다
                    #   (`evidence_item` 규율). 개수를 넣으면 "왜 그날이 위험일인가" 에 아무
                    #   답이 안 된다.
                    "critical_payment_dates",
                    policy.minimum_cash_balance_krw,
                    "KRW",
                    minimum_cash_ref,
                    f"이 임계 미만으로 떨어지는 지급일 {len(critical_dates)} 건 "
                    f"(D+{policy.cashflow_projection_days} 투영)",
                    grade="SIM_FIXED",
                ),
            )

    reply = AgentReply(
        request_id=request.context.request_id,
        as_of=as_of,
        agent="finance",
        mode=request.mode,
        run_id=run_id,
        runtime_status="READY",
        business_status="ok",
        payload=payload,
        evidences=evidences,
        judgment_fields=JUDGMENT_FIELDS if "payment_pressure" in payload else (),
        missing_data=tuple(missing),
        reasoning=messages.STATUS_QUERY if not missing else messages.STATUS_QUERY_PARTIAL,
    )
    return reply, adapter_metadata(request, run_id, tools)
