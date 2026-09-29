"""PRE_SALES_FACTS — 판매 후보를 만들기 **전에** 내는 재무 사실. 읽기만 한다.

★ 2026-09-29 재구성 BL-014: `finance/adapter.py` 에서 옮겼다(몸통 그대로).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.contracts.core import Evidence
from app.contracts.envelope import AgentReply, AgentRequest, ExecutionMetadata
from app.finance.domain import messages
from app.finance.domain.evidence import PAYROLL_SOURCE_KEYS
from app.finance.domain.pre_sales import (
    build_obligation_facts,
    build_partner_credit_facts,
    partner_credit_limit_ref,
)
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
    summarize_partner_receivables,
)
from app.finance.readmodel.partner_credit import load_partner_credit_limit, load_partner_receivables
from app.finance.schemas.agent_state import missing_source_name
from app.finance.schemas.data_port import FinanceDataNotReady
from app.finance.service.agent_replies import (
    adapter_metadata,
    axis_not_ready_reply,
    new_run_id,
    not_ready_reply,
)
from app.finance.service.agent_run import load_runtime_context

# ---------------------------------------------------------------------------
# PRE_SALES_FACTS — 판매 후보를 만들기 **전에** 내는 사실
# ---------------------------------------------------------------------------


def answer_pre_sales_facts(request: AgentRequest) -> tuple[AgentReply, ExecutionMetadata]:
    """판매가 후보를 만들기 전에 볼 재무 사실. **읽기만 한다.**

    ★ **`SALES_VALIDATION` 을 재사용하지 않는다.** 저쪽은 후보 하나를 받아 판정하고,
      여기는 후보가 아직 없다. 한 mode 로 합치면 *"후보 없이 불린 검증"* 이라는 모양이
      생기고, 그 모양을 받아들이는 순간 판정 안 난 실행이 판정된 것으로 읽힌다.

    ★ **`answer_status_query` 와 같은 태도로 부분 답을 낸다.** 급여 출처가 없으면 투영이
      필요한 값만 빼고 현재 잔액·채무·채권은 그대로 답한다 — 여기서 통째로 멈추면
      판매 후보 생성이 **매입 급여 정책 때문에** 막힌다.

    🔴 **실행 이력을 쓰지 않는다** (`recorded_reply` 의 `_CONTROLLER_MODES` 에 없다).
      사실 조회가 장부를 건드리지 않는다는 것이 이 mode 의 계약이다.

    🔴 **없는 값을 `0` 으로 채우지 않는다.** 못 읽은 이름은 `missing_data` 로 나가고
      칸 자체를 만들지 않는다 — 판매가 *"0원이라는 사실"* 과 구별할 수 있어야 한다.
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
            request, run_id, tools,
            missing=("finance_state", "finance_policy"),
            reason=messages.CONTEXT_UNAVAILABLE,
        )
    if context.snapshot.state_date != as_of:
        return not_ready_reply(
            request, run_id, tools,
            missing=(f"finance_state@{as_of.isoformat()}",),
            reason=messages.AS_OF_MISMATCH,
        )

    policy = context.policy
    ref = context.snapshot.finance_state_id
    missing: list[str] = []
    payload: dict[str, Any] = {
        "as_of": as_of.isoformat(),
        "state_date": context.snapshot.state_date.isoformat(),
        "available_cash": as_float(context.snapshot.current_cash_krw),
        "policy_version_used": policy.policy_version,
    }
    evidences: tuple[Evidence, ...] = (
        evidence_item(
            "available_cash", context.snapshot.current_cash_krw, "KRW", ref, "재무 상태 현재 잔액"
        ),
    )

    obligations = [event for event in context.cash_events if event.direction == "OUTFLOW"]
    receivables = [event for event in context.cash_events if event.direction == "INFLOW"]
    obligation_facts = build_obligation_facts(
        as_of=as_of, obligations=obligations, receivables=receivables
    )
    for name, amount in obligation_facts.items():
        payload[name] = as_float(amount)
        evidences = (
            *evidences,
            evidence_item(
                name,
                amount,
                "KRW",
                ref,
                f"D+{policy.cashflow_projection_days} 구간의 확정 현금 일정 집계",
            ),
        )

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
        # 투영이 필요한 값만 뺀다. 잔액·채무·채권·여신은 그대로 답한다.
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
        # ★ **이름이 `base_` 다.** 판매 제안을 아직 안 얹은 투영이라는 뜻이고,
        #   제안을 얹은 값(`scenario_projected_cash_min`)은 검증이 낸다.
        payload["base_projected_cash_min"] = as_float(cash_min)
        payload["payment_pressure"] = pressure
        payload["projection_days"] = policy.cashflow_projection_days
        evidences = (
            *evidences,
            evidence_item(
                "base_projected_cash_min",
                cash_min,
                "KRW",
                ref,
                f"D+{policy.cashflow_projection_days} 투영 최저 (판매 제안 미반영)",
            ),
            evidence_item(
                "payment_pressure",
                safe_ratio(cash_min, policy.minimum_cash_balance_krw),
                "ratio",
                ref,
                f"투영최저/최소현금 = 임계 {policy.cash_priority_high_ratio}"
                f"/{policy.cash_priority_medium_ratio} → {pressure}",
            ),
            evidence_item(
                "projection_days",
                policy.cashflow_projection_days,
                "day",
                ref,
                "현금 투영 Horizon",
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
                    "critical_payment_dates",
                    policy.minimum_cash_balance_krw,
                    "KRW",
                    minimum_cash_ref,
                    f"이 임계 미만으로 떨어지는 지급일 {len(critical_dates)} 건 "
                    f"(D+{policy.cashflow_projection_days} 투영)",
                    grade="SIM_FIXED",
                ),
            )

    credit_payload, credit_missing, credit_evidences = _pre_sales_partner_credit(request, as_of)
    if credit_payload:
        # ★ **중첩 칸이다.** 거래처 id 는 대문자 라벨 모양이라 최상위에 두면 봉투가
        #   *"판정 라벨"* 로 읽고 근거를 요구한다 — 거래처 이름은 판정이 아니다.
        payload["partner_credit"] = credit_payload
        evidences = (*evidences, *credit_evidences)
    missing.extend(credit_missing)

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
        missing_data=tuple(dict.fromkeys(missing)),
        reasoning=(
            messages.STATUS_QUERY if not missing else messages.STATUS_QUERY_PARTIAL
        ),
    )
    return reply, adapter_metadata(request, run_id, tools)


def _pre_sales_partner_credit(
    request: AgentRequest, as_of: date
) -> tuple[dict[str, Any], list[str], tuple[Evidence, ...]]:
    """거래처 채권·여신 사실. **못 읽으면 못 읽은 채로 돌려준다.**

    ★ 조회 실패를 `0` 으로 바꾸지 않는다. `FinanceDataNotReady` 는 *"못 읽었다"* 이고
      빈 채권 목록은 *"채권이 0원이다"* 인데, 둘을 같은 값으로 만들면 여신이 가득
      찬 거래처가 새 거래처처럼 보인다.
    """
    partner_id = request.payload.get("partner_id")
    partner_id = None if partner_id is None else str(partner_id)
    receivable_facts = None
    credit_limit: Decimal | None = None
    missing: list[str] = []
    if partner_id and partner_id.strip():
        try:
            receivable_facts = summarize_partner_receivables(
                partner_id=partner_id,
                as_of=as_of,
                receivables=load_partner_receivables(
                    sim_run_id=request.context.sim_run_id, as_of=as_of, partner_id=partner_id
                ),
            )
        except (FinanceDataNotReady, ValueError):
            receivable_facts = None
        try:
            credit_limit = load_partner_credit_limit(as_of=as_of, partner_id=partner_id)
        except (FinanceDataNotReady, ValueError):
            credit_limit = None

    facts, facts_missing = build_partner_credit_facts(
        partner_id=partner_id,
        receivable_facts=receivable_facts,
        credit_limit_krw=credit_limit,
    )
    missing.extend(facts_missing)

    evidences: tuple[Evidence, ...] = ()
    if receivable_facts is not None:
        receivable_ref = (
            receivable_facts.source_refs[0]
            if receivable_facts.source_refs
            # ★ 채권이 0원이면 가리킬 행이 없다. 그때는 **어느 조회였는지**를 가리킨다.
            else f"receivables(partner_id={partner_id},as_of={as_of.isoformat()})"
        )
        evidences = (
            *evidences,
            evidence_item(
                "partner_credit.partner_receivable_krw",
                receivable_facts.current_ar_krw,
                "KRW",
                receivable_ref,
                f"미회수 채권 {receivable_facts.open_receivable_count} 건 합계",
            ),
        )
    if credit_limit is not None and partner_id:
        evidences = (
            *evidences,
            evidence_item(
                "partner_credit.partner_credit_limit_krw",
                credit_limit,
                "KRW",
                partner_credit_limit_ref(partner_id=partner_id, as_of=as_of),
                "그날 유효한 거래처 여신한도",
            ),
        )
    payload = {
        name: (as_float(value) if isinstance(value, Decimal) else value)
        for name, value in facts.items()
    }
    return payload, missing, evidences
