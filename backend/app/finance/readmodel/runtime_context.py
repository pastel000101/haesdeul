"""재무 런타임 컨텍스트 — Snapshot · Policy · 확정 일정을 한 번에 고정한다.

조회 연결 하나로 같은 순서로 읽는다.
"""

from datetime import date, timedelta
from decimal import Decimal

from app.core import db as core_db
from app.finance.domain.cash_events import payable_cash_events, rows_to_events
from app.finance.domain.policy_rules import build_finance_debt_policy, build_finance_policy
from app.finance.domain.tools import build_debt_service_schedule
from app.finance.readmodel.finance_state import finance_snapshot_on
from app.finance.repository.cash_events import (
    select_accrued_expenses,
    select_open_payables,
    select_scheduled_rows,
)
from app.finance.repository.policy import select_debt_policy_rows, select_policy_rows
from app.finance.schemas.agent import CashEvent, FinanceRuntimeContext


def get_current_finance_runtime_context(
    as_of: date | None = None, *, sim_run_id: str | None = None
) -> FinanceRuntimeContext:
    """Snapshot, Policy, 확정 일정을 DB 경계에서 한 번 고정한다.

    ``as_of`` 는 어느 상태 행을 고를지만 정한다. 고른 뒤의 투영 기준일은 그대로 그 행의
    ``state_date`` 다 — 상태가 적힌 날의 잔액을 다른 날 잔액으로 옮겨 쓰지 않는다. 어긋나면
    위(`service/agent_run.py::controller_boundary`)에서 닫는다.

    `sim_run_id` 를 주면 그 실행의 상태만 고른다. 실행이 여럿인 환경에서 축을 안 주면 어느
    실행의 잔액인지 말할 수 없다.

    조회 연결을 한 번 빌려 같은 순서로 읽는다.
    """
    with core_db.read_connection() as conn:
        snapshot = finance_snapshot_on(conn, as_of, sim_run_id=sim_run_id)
        policy = build_finance_policy(select_policy_rows(conn))
        horizon_end = snapshot.state_date + timedelta(days=policy.cashflow_projection_days)
        events: list[CashEvent] = []
        unresolved: list[str] = []

        payable_rows = select_open_payables(
            conn, sim_run_id=snapshot.sim_run_id, horizon_end=horizon_end
        )
        events.extend(payable_cash_events(payable_rows, as_of=snapshot.state_date))
        if snapshot.unsettled_purchase_payables_krw != 0 and not payable_rows:
            unresolved.append("PURCHASE_PAYABLE")

        expense_rows = select_accrued_expenses(
            conn,
            sim_run_id=snapshot.sim_run_id,
            as_of=snapshot.state_date,
            horizon_end=horizon_end,
        )
        events.extend(
            rows_to_events(
                expense_rows,
                id_column="expense_id",
                date_column="effective_due_date",
                amount_column="amount_krw",
                event_type="COMMITTED_OUTFLOW",
                direction="OUTFLOW",
            )
        )
        if snapshot.committed_outflows_krw != 0 and not expense_rows:
            unresolved.append("COMMITTED_OUTFLOW")

        receivable_rows = select_scheduled_rows(
            conn,
            table="receivables",
            columns=("receivable_id", "due_date", "outstanding_amount_krw"),
            sim_run_id=snapshot.sim_run_id,
            as_of=snapshot.state_date,
            horizon_end=horizon_end,
            status_column="status",
            active_status="OPEN",
        )
        events.extend(
            rows_to_events(
                receivable_rows,
                id_column="receivable_id",
                date_column="due_date",
                amount_column="outstanding_amount_krw",
                event_type="RECEIVABLE",
                direction="INFLOW",
            )
        )
        if snapshot.receivables_krw != 0 and not receivable_rows:
            unresolved.append("RECEIVABLE")

        # 부채가 없으면 부채 정책을 요구하지 않는다.
        #
        # `current_debt_krw` 와 무관하게 부채 정책을 읽고 행이 없을 때 `DEBT_SERVICE` 를
        # unresolved 로 올리면, 빚이 없는 회사가 "부채 원천을 확인하지 못했다" 고 말하게
        # 된다 — 확인할 부채가 애초에 없는데도. 그 unresolved 는 아래로 흘러 "재무가 뭔가 못
        # 읽었다" 로 읽히고, 실제로는 아무 문제가 없다. 없는 의무를 증명하라고 요구하는 셈이다.
        #
        # 부채가 있으면 규율은 그대로다 — 정책이 없거나 원금이 상태와 어긋나면 fail-closed
        # 다. 부채 상환은 현금흐름에서 가장 확실한 유출이라, 그것을 빠뜨린 투영은 틀린 게
        # 아니라 낙관적으로 틀린다.
        debt_policy = None
        if snapshot.current_debt_krw > 0:
            try:
                debt_policy = build_finance_debt_policy(select_debt_policy_rows(conn))
            except (LookupError, TypeError, ValueError):
                unresolved.append("DEBT_SERVICE")
            if debt_policy is not None:
                if abs(
                    debt_policy.debt_principal_krw - snapshot.current_debt_krw
                ) > Decimal("0.000001"):
                    unresolved.append("DEBT_SERVICE")
                    debt_policy = None
                else:
                    events.extend(
                        build_debt_service_schedule(
                            debt_policy=debt_policy,
                            as_of=snapshot.state_date,
                            horizon_end=horizon_end,
                        )
                    )

    return FinanceRuntimeContext(
        snapshot=snapshot,
        policy=policy,
        debt_policy=debt_policy,
        cash_events=tuple(events),
        unresolved_sources=tuple(unresolved),
    )
