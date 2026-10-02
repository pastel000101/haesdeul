"""Finance 화면용 DB 조회와 dashboard 응답 조립.

SQL 은 `repository/dashboard.py`, 응답 모델은 `schemas/dashboard.py`. 조회 연결을 한 번 빌려
같은 순서로 읽는다.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from app.core import db as core_db
from app.core.text import decimal_or_zero
from app.finance.repository.dashboard import (
    load_cashflow,
    load_cashflow_summary,
    load_expense_summary,
    load_finance_dashboard_meta,
    load_finance_states,
    load_payable_summary,
    load_payables,
    load_receivable_summary,
    load_receivables,
    load_recent_closings,
)
from app.finance.schemas.dashboard import (
    FinanceCashflowResponse,
    FinanceCashflowSummary,
    FinanceClosingItem,
    FinanceDashboardMeta,
    FinanceDashboardResponse,
    FinanceExpenseSummary,
    FinancePayableItem,
    FinancePayableSummary,
    FinanceReceivableItem,
    FinanceReceivableSummary,
    FinanceStateView,
)


def get_finance_dashboard(
    *, sim_run_id: str, as_of: date, recent_limit: int = 10
) -> FinanceDashboardResponse:
    """재무 현황 한 판. 조회 연결을 한 번 빌려 같은 순서로 읽고 응답으로 편다."""
    with core_db.read_connection() as conn:
        meta = _dashboard_meta(conn, sim_run_id=sim_run_id, as_of=as_of)
        state_rows = load_finance_states(conn, sim_run_id=sim_run_id, as_of=as_of)
        receivable_summary = _receivable_summary(
            load_receivable_summary(conn, sim_run_id=sim_run_id, as_of=as_of)
        )
        payable_summary = _payable_summary(
            load_payable_summary(conn, sim_run_id=sim_run_id, as_of=as_of)
        )
        cashflow_summary = _cashflow_summary(
            load_cashflow_summary(conn, sim_run_id=sim_run_id, as_of=as_of)
        )
        receivables = load_receivables(conn, sim_run_id=sim_run_id, as_of=as_of)
        payables = load_payables(conn, sim_run_id=sim_run_id, as_of=as_of)
        expenses = load_expense_summary(conn, sim_run_id=sim_run_id, as_of=as_of)
        recent_closings = load_recent_closings(
            conn, sim_run_id=sim_run_id, as_of=as_of, limit=recent_limit
        )
    return FinanceDashboardResponse(
        meta=meta,
        states=_states(state_rows),
        cashflow_summary=cashflow_summary,
        ledger_summary={"receivables": receivable_summary, "payables": payable_summary},
        receivables=[FinanceReceivableItem.model_validate(row) for row in receivables],
        payables=[FinancePayableItem.model_validate(row) for row in payables],
        expenses=[FinanceExpenseSummary.model_validate(row) for row in expenses],
        recent_closings=_closings(recent_closings, states=state_rows),
    )


def get_finance_cashflow(
    *, sim_run_id: str, as_of: date, days: int = 30
) -> FinanceCashflowResponse:
    """최근 일마감 흐름. 조회 연결을 한 번 빌려 같은 순서로 읽는다."""
    with core_db.read_connection() as conn:
        states = load_finance_states(conn, sim_run_id=sim_run_id, as_of=as_of)
        meta = _dashboard_meta(conn, sim_run_id=sim_run_id, as_of=as_of)
        closings = load_cashflow(conn, sim_run_id=sim_run_id, as_of=as_of, days=days)
    return FinanceCashflowResponse(
        meta=meta,
        cashflow=_closings(closings, states=states),
    )


def _dashboard_meta(conn: Any, *, sim_run_id: str, as_of: date) -> FinanceDashboardMeta:
    row = load_finance_dashboard_meta(conn, sim_run_id=sim_run_id, as_of=as_of)
    return FinanceDashboardMeta(
        sim_run_id=sim_run_id,
        as_of=as_of,
        data_type=None if row is None else str(row["data_type"]),
    )


def _states(rows: list[dict[str, object]]) -> list[FinanceStateView]:
    result = []
    for row in rows:
        cash = decimal_or_zero(row["current_cash_krw"])
        minimum = decimal_or_zero(row["minimum_operating_cash_krw"])
        result.append(
            FinanceStateView(
                **row,
                operating_cash_buffer_krw=cash - minimum,
            )
        )
    return result


def _cashflow_summary(row: dict[str, object] | None) -> FinanceCashflowSummary:
    row = row or {}
    return FinanceCashflowSummary(
        purchase_cash_out_krw=decimal_or_zero(row.get("purchase_cash_out_krw")),
        logistics_cash_out_krw=decimal_or_zero(row.get("logistics_cash_out_krw")),
        payroll_interest_cash_out_krw=decimal_or_zero(row.get("payroll_interest_cash_out_krw")),
        operating_expense_cash_out_krw=decimal_or_zero(row.get("operating_expense_cash_out_krw")),
        sales_recognized_krw=decimal_or_zero(row.get("sales_recognized_krw")),
        collection_cash_in_krw=decimal_or_zero(row.get("collection_cash_in_krw")),
        base_net_cash_krw=decimal_or_zero(row.get("base_net_cash_krw")),
        loan_execution_krw=decimal_or_zero(row.get("loan_execution_krw")),
    )


def _receivable_summary(row: dict[str, object] | None) -> FinanceReceivableSummary:
    row = row or {}
    return FinanceReceivableSummary(
        count=int(row.get("count") or 0),
        collected_count=int(row.get("collected_count") or 0),
        partial_count=int(row.get("partial_count") or 0),
        open_count=int(row.get("open_count") or 0),
        original_amount_krw=decimal_or_zero(row.get("original_amount_krw")),
        received_amount_krw=decimal_or_zero(row.get("received_amount_krw")),
        outstanding_amount_krw=decimal_or_zero(row.get("outstanding_amount_krw")),
        overdue_amount_krw=decimal_or_zero(row.get("overdue_amount_krw")),
    )


def _payable_summary(row: dict[str, object] | None) -> FinancePayableSummary:
    row = row or {}
    return FinancePayableSummary(
        count=int(row.get("count") or 0),
        original_amount_krw=decimal_or_zero(row.get("original_amount_krw")),
        paid_amount_krw=decimal_or_zero(row.get("paid_amount_krw")),
        outstanding_amount_krw=decimal_or_zero(row.get("outstanding_amount_krw")),
        overdue_amount_krw=decimal_or_zero(row.get("overdue_amount_krw")),
    )


def _closings(
    rows: list[dict[str, object]], *, states: list[dict[str, object]]
) -> list[FinanceClosingItem]:
    minimum = _minimum_cash(states)
    return [
        FinanceClosingItem(
            **_closing_payload(row),
            minimum_operating_cash_krw=minimum,
            base_operating_buffer_krw=None
            if minimum is None
            else decimal_or_zero(row["base_cash_balance_krw"]) - minimum,
            loan_operating_buffer_krw=None
            if minimum is None
            else decimal_or_zero(row["loan_cash_balance_krw"]) - minimum,
        )
        for row in rows
    ]


def _closing_payload(row: dict[str, object]) -> dict[str, object]:
    return {
        key: row[key]
        for key in (
            "close_date",
            "day_no",
            "purchase_cash_out_krw",
            "logistics_cash_out_krw",
            "payroll_interest_cash_out_krw",
            "operating_expense_cash_out_krw",
            "sales_recognized_krw",
            "collection_cash_in_krw",
            "base_net_cash_krw",
            "base_cash_balance_krw",
            "loan_execution_krw",
            "loan_cash_balance_krw",
            "receivables_balance_krw",
            "inventory_qty_kg",
            "accounting_inventory_cost_krw",
        )
    }


def _minimum_cash(rows: list[dict[str, object]]) -> Decimal | None:
    for row in rows:
        if row.get("financing_mode") == "BASE_NO_LOAN":
            return decimal_or_zero(row.get("minimum_operating_cash_krw"))
    if rows:
        return decimal_or_zero(rows[0].get("minimum_operating_cash_krw"))
    return None
