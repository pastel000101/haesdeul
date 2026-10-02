"""판매 현황(대시보드) 응답 — `readmodel/dashboard.py` 가 채운다."""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Dashboard 조회 응답
# ---------------------------------------------------------------------------

class SalesDashboardMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sim_run_id: str
    as_of: date
    data_type: str | None = None


class SalesDashboardSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sales_count: int
    customer_count: int
    total_sales_quantity_kg: Decimal
    total_sales_amount_krw: Decimal
    contribution_profit_krw: Decimal
    contribution_margin_pct: Decimal
    received_amount_krw: Decimal
    outstanding_receivables_krw: Decimal


class SalesCollectionStatusSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int
    sales_amount_krw: Decimal


class SalesItemSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    item_name: str
    line_count: int
    total_quantity_kg: Decimal
    sales_amount_krw: Decimal
    contribution_profit_krw: Decimal
    contribution_margin_pct: Decimal
    avg_unit_price_krw_per_kg: Decimal


class SalesHistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sale_id: str
    order_date: date
    sale_date: date
    customer_partner_id: str
    partner_name: str | None = None
    total_quantity_kg: Decimal
    total_amount_krw: Decimal
    contribution_profit_krw: Decimal
    contribution_margin_pct: Decimal
    collection_due_date: date
    collection_status: str
    collection_status_label: str
    order_status: str


class TodayConfirmedSaleItem(BaseModel):
    """기준일에 판매를 확정한 원장 품목 행.

    ``order_date`` 는 판매 확정일이고 ``sale_date`` 는 납품 예정일이다. 두 날짜를
    합쳐 읽으면 미래 납품 주문이 확정 목록에서 사라진다.
    """

    model_config = ConfigDict(extra="forbid")

    sale_id: str
    order_date: date
    sale_date: date
    customer_partner_id: str
    partner_name: str | None = None
    item_id: str
    item_name: str | None = None
    quantity_kg: Decimal
    unit_price_krw_per_kg: Decimal
    line_amount_krw: Decimal
    order_status: str


class SalesReceivableItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receivable_id: str
    sale_id: str
    sale_date: date
    customer_partner_id: str
    partner_name: str | None = None
    issued_date: date
    due_date: date
    original_amount_krw: Decimal
    received_amount_krw: Decimal
    outstanding_amount_krw: Decimal
    status: str
    display_status: str
    d_day: int | None


class SalesDashboardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meta: SalesDashboardMeta
    summary: SalesDashboardSummary
    collection_summary: dict[str, SalesCollectionStatusSummary]
    items: list[SalesItemSummary]
    recent_sales: list[SalesHistoryItem]
    today_confirmed_sales: list[TodayConfirmedSaleItem] = Field(default_factory=list)
    receivables: list[SalesReceivableItem]
