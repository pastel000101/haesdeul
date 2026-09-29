"""as-of 재현성을 지키는 DataPort 구현.

★ 2026-09-29 재구성 BL-014: `finance/db.py` 에서 옮겼다. 메서드마다 조회 연결을 한 번 빌린다.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from app.core import db as core_db
from app.finance.domain.cash_events import payable_cash_events, rows_to_events
from app.finance.domain.policy_rules import build_finance_debt_policy
from app.finance.domain.tools import build_debt_service_schedule
from app.finance.readmodel.finance_state import current_state_row_on
from app.finance.readmodel.partner_credit import load_partner_credit_limit, load_partner_receivables
from app.finance.readmodel.policy import get_active_finance_policy
from app.finance.repository.cash_events import (
    select_accrued_expenses,
    select_open_payables,
    select_scheduled_rows,
)
from app.finance.repository.policy import select_debt_policy_rows
from app.finance.schemas.agent import CashEvent, FinancePolicy
from app.finance.schemas.data_port import FinanceDataNotReady
from app.finance.schemas.policy import FINANCE_POLICY_VERSION
from app.finance.schemas.sales_validation import PartnerReceivable

# ---------------------------------------------------------------------------
# as-of 재현성을 지키는 DataPort 구현
# ---------------------------------------------------------------------------

class PostgresFinanceAsOfDataPort:
    """명시적인 재현성 보호 장치를 둔 현재 Schema용 Adapter.

    상태 선택은 `load_finance_state_row` 가 ``as_of`` 로 한다 — 고정된 한 행이
    아니라 그 시점에 유효한 행이다. 그 위에 **잔액을 옮겨 쓰지 않는** 보호를 한 겹
    더 둔다: 고른 행의 날짜가 ``as_of`` 와 다르면 그날 잔액을 모르는 것이므로
    준비되지 않은 것으로 보고한다.

    ★ 2026-09-29 재구성 BL-014: 메서드 하나가 조회 연결을 **한 번** 빌려 필요한 SQL 을
      같은 순서로 읽는다.
    """

    def __init__(self, *, sim_run_id: str | None = None) -> None:
        #: 이 DataPort 가 읽는 실행. **주면 그 실행만 본다** — 실행이 여럿인 환경에서
        #: 축을 안 주면 어느 실행의 잔액인지 말할 수 없다.
        self.sim_run_id = sim_run_id
        self._position_cache: tuple[date, dict[str, object]] | None = None
        self._policy_cache: tuple[date, str, FinancePolicy] | None = None

    def load_finance_position(self, as_of: date) -> dict[str, object]:
        if self._position_cache is not None and self._position_cache[0] == as_of:
            return self._position_cache[1]
        with core_db.read_connection() as conn:
            return self._position_on(conn, as_of)

    def _position_on(self, conn: Any, as_of: date) -> dict[str, object]:
        if self._position_cache is not None and self._position_cache[0] == as_of:
            return self._position_cache[1]
        row = current_state_row_on(conn, as_of, sim_run_id=self.sim_run_id)
        if row.get("state_date") != as_of:
            raise FinanceDataNotReady("historical_finance_position")
        self._position_cache = (as_of, row)
        return row

    def load_policy(self, as_of: date, policy_version: str) -> FinancePolicy:
        if self._policy_cache is not None and self._policy_cache[:2] == (
            as_of,
            policy_version,
        ):
            return self._policy_cache[2]
        if policy_version != FINANCE_POLICY_VERSION:
            raise FinanceDataNotReady("finance_policy_version")
        try:
            policy = get_active_finance_policy()
        except (LookupError, TypeError, ValueError) as exc:
            raise FinanceDataNotReady("finance_policy") from exc
        self._policy_cache = (as_of, policy_version, policy)
        return policy


    def load_partner_receivables(self, as_of: date, partner_id: str) -> list[PartnerReceivable]:
        """이 실행의 sim_run 과 as_of 안에서만 거래처 채권을 읽는다.

        ★ `load_finance_position` 을 먼저 통과한다 — 과거 시점을 오늘 상태로 대신
          답하지 않는 보호가 채권에도 그대로 걸려야 한다.
        """
        position = self.load_finance_position(as_of)
        return load_partner_receivables(
            sim_run_id=str(position["sim_run_id"]), as_of=as_of, partner_id=partner_id
        )

    def load_partner_credit_limit(self, as_of: date, partner_id: str) -> Decimal | None:
        """그날 유효한 거래처 여신한도.

        ★ **실행 축을 걸지 않는다.** 여신한도는 거래처와 계약이 소유한 사실이고 어느
          시뮬레이션에서 보든 같다 — `sim_run_id` 로 좁히면 실행마다 다른 한도가
          있는 것처럼 읽힌다. 시점만 `as_of` 로 자른다.
        """
        return load_partner_credit_limit(as_of=as_of, partner_id=partner_id)

    def load_obligations(self, as_of: date, horizon: date) -> list[CashEvent]:
        with core_db.read_connection() as conn:
            position = self._position_on(conn, as_of)
            payable_rows = select_open_payables(
                conn, sim_run_id=str(position["sim_run_id"]), horizon_end=horizon
            )
            expense_rows = select_accrued_expenses(
                conn,
                sim_run_id=str(position["sim_run_id"]),
                as_of=as_of,
                horizon_end=horizon,
            )
        return [
            *payable_cash_events(payable_rows, as_of=as_of),
            *rows_to_events(
                expense_rows,
                id_column="expense_id",
                date_column="effective_due_date",
                amount_column="amount_krw",
                event_type="COMMITTED_OUTFLOW",
                direction="OUTFLOW",
            ),
        ]

    def load_receivables(self, as_of: date, horizon: date) -> list[CashEvent]:
        with core_db.read_connection() as conn:
            position = self._position_on(conn, as_of)
            rows = select_scheduled_rows(
                conn,
                table="receivables",
                columns=("receivable_id", "due_date", "outstanding_amount_krw"),
                sim_run_id=str(position["sim_run_id"]),
                as_of=as_of,
                horizon_end=horizon,
                status_column="status",
                active_status="OPEN",
            )
        return rows_to_events(
            rows,
            id_column="receivable_id",
            date_column="due_date",
            amount_column="outstanding_amount_krw",
            event_type="RECEIVABLE",
            direction="INFLOW",
        )

    def load_payroll(self, as_of: date, horizon: date) -> Decimal | None:
        del horizon
        if self._policy_cache is None or self._policy_cache[0] != as_of:
            raise FinanceDataNotReady("finance_policy_context")
        policy = self._policy_cache[2]
        return policy.monthly_labor_cost_krw

    def load_debt_schedule(self, as_of: date, horizon: date) -> list[CashEvent]:
        with core_db.read_connection() as conn:
            position = self._position_on(conn, as_of)
            try:
                debt = build_finance_debt_policy(select_debt_policy_rows(conn))
            except (LookupError, TypeError, ValueError) as exc:
                raise FinanceDataNotReady("debt_policy") from exc
        if abs(
            debt.debt_principal_krw - Decimal(str(position["current_debt_krw"]))
        ) > Decimal("0.000001"):
            raise FinanceDataNotReady("debt_policy_consistency")
        return list(build_debt_service_schedule(debt_policy=debt, as_of=as_of, horizon_end=horizon))
