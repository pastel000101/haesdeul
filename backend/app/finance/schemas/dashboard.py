"""재무 화면 조회 응답 — 재무 현황 · 현금 흐름 한 판.

★ 2026-09-29 재구성 BL-014: `finance/schemas.py` 의 화면 조회 절을 옮겼다(필드 · 모양 그대로).
  채우는 쪽은
  `readmodel/dashboard.py`, SQL 은 `repository/dashboard.py` 다.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# Dashboard 조회 응답
# ---------------------------------------------------------------------------

class FinanceDashboardMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sim_run_id: str
    as_of: date
    data_type: str | None = None


class FinanceStateView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finance_state_id: str
    state_date: date
    state_type: str
    financing_mode: str
    current_cash_krw: Decimal
    minimum_operating_cash_krw: Decimal
    operating_cash_buffer_krw: Decimal
    committed_outflows_krw: Decimal
    unsettled_purchase_payables_krw: Decimal
    receivables_krw: Decimal
    inventory_book_value_krw: Decimal
    operational_inventory_value_krw: Decimal
    current_debt_krw: Decimal
    financial_limit_krw: Decimal
    recommended_loan_amount_krw: Decimal | None = None
    note: str | None = None


class FinanceCashflowSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purchase_cash_out_krw: Decimal
    logistics_cash_out_krw: Decimal
    payroll_interest_cash_out_krw: Decimal
    #: 기간 합이라 **기록된 날만 더한다.** 기록하지 않은 날은 0 으로 세지 않고 빠진다.
    operating_expense_cash_out_krw: Decimal = Decimal(0)
    sales_recognized_krw: Decimal
    collection_cash_in_krw: Decimal
    base_net_cash_krw: Decimal
    loan_execution_krw: Decimal


class FinanceReceivableSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int
    collected_count: int
    partial_count: int
    open_count: int
    original_amount_krw: Decimal
    received_amount_krw: Decimal
    outstanding_amount_krw: Decimal
    overdue_amount_krw: Decimal


class FinancePayableSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int
    original_amount_krw: Decimal
    paid_amount_krw: Decimal
    outstanding_amount_krw: Decimal
    overdue_amount_krw: Decimal


class FinanceReceivableItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receivable_id: str
    sale_id: str
    issued_date: date
    due_date: date
    original_amount_krw: Decimal
    received_amount_krw: Decimal
    outstanding_amount_krw: Decimal
    status: str


class FinancePayableItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payable_id: str
    purchase_id: str
    issued_date: date
    due_date: date
    original_amount_krw: Decimal
    paid_amount_krw: Decimal
    outstanding_amount_krw: Decimal
    status: str


class FinanceExpenseSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expense_category: str
    status: str
    expense_count: int
    total_amount_krw: Decimal
    fixed_amount_krw: Decimal
    variable_amount_krw: Decimal


class FinanceClosingItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    close_date: date
    day_no: int
    purchase_cash_out_krw: Decimal
    logistics_cash_out_krw: Decimal
    payroll_interest_cash_out_krw: Decimal
    #: 일반 운영비 현금유출. `base_net_cash_krw` 는 이 값까지 빼고 적힌 값이다.
    #:
    #: 🔴 **`None` 은 «그 실행이 이 축을 기록하지 않았다» 다 — 0원이 아니다.** 이 칸이
    #:    생기기 전 마감에 0 을 적으면 «세어 보니 없었다» 가 되고, 그러면 아무도 그날
    #:    운영비가 정말 없었는지 물어보지 않는다.
    operating_expense_cash_out_krw: Decimal | None = None
    sales_recognized_krw: Decimal
    collection_cash_in_krw: Decimal
    base_net_cash_krw: Decimal
    base_cash_balance_krw: Decimal
    loan_execution_krw: Decimal
    loan_cash_balance_krw: Decimal
    minimum_operating_cash_krw: Decimal | None = None
    base_operating_buffer_krw: Decimal | None = None
    loan_operating_buffer_krw: Decimal | None = None
    receivables_balance_krw: Decimal
    inventory_qty_kg: Decimal
    accounting_inventory_cost_krw: Decimal


class FinanceDashboardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meta: FinanceDashboardMeta
    states: list[FinanceStateView]
    cashflow_summary: FinanceCashflowSummary
    ledger_summary: dict[str, FinanceReceivableSummary | FinancePayableSummary]
    receivables: list[FinanceReceivableItem]
    payables: list[FinancePayableItem]
    expenses: list[FinanceExpenseSummary]
    recent_closings: list[FinanceClosingItem]


class FinanceCashflowResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meta: FinanceDashboardMeta
    cashflow: list[FinanceClosingItem]
