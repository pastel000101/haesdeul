"""판매 운영 콘솔의 거래처 응답 — `readmodel/console_partners.py` 가 채운다.

★ 2026-09-29 BL-013: `sales/console_partners.py` 에서 옮겼다.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from app.contracts.aging import AgingBucket

_ZERO = Decimal(0)


class ConsolePartnerRow(BaseModel):
    partner_id: str
    partner_name: str | None
    partner_type: str | None
    #: The stored `partners.active` flag, named for the screen.  Not a sales status.
    status: str
    total_sales_krw: Decimal
    total_sales_count: int
    receivable_balance_krw: Decimal
    overdue_balance_krw: Decimal
    #: Null means this partner has no sale in this run — not "no partner".
    latest_sale_date: date | None


class ConsolePartnersResponse(BaseModel):
    sim_run_id: str
    as_of: date
    rows: list[ConsolePartnerRow]


class ConsolePartnerBasic(BaseModel):
    partner_id: str
    partner_name: str | None
    partner_type: str | None
    client_type: str | None
    factory_region: str | None
    sales_collection_days: int | None
    pricing_contract_type: str | None
    status: str


class ConsolePartnerSummary(BaseModel):
    total_sales_krw: Decimal = _ZERO
    sales_count: int = 0
    contribution_profit_krw: Decimal = _ZERO
    #: Null when there is no turnover to divide by.  Zero would claim a measured
    #: margin of 0%, which is a different statement from "nothing to measure".
    contribution_margin_rate: Decimal | None = None
    receivable_balance_krw: Decimal = _ZERO
    overdue_balance_krw: Decimal = _ZERO
    latest_sale_date: date | None = None


class ConsolePartnerSaleRow(BaseModel):
    sale_id: str
    sale_date: date
    #: 내부 품목 코드. 화면 기본값이 아니라 기술 상세용이다.
    item: str | None
    #: 사람이 읽는 품목 이름. `items` 에 없으면 `None` — 코드로 대신 채우지 않는다.
    item_name: str | None = None
    quantity_kg: Decimal
    unit_price_krw: Decimal | None
    sales_amount_krw: Decimal
    contribution_profit_krw: Decimal | None


class ConsolePartnerItemRow(BaseModel):
    #: 내부 품목 코드.
    item: str
    #: 사람이 읽는 품목 이름. 없으면 `None` 이고 화면이 코드를 대신 쓴다.
    item_name: str | None = None
    quantity_kg: Decimal
    sales_amount_krw: Decimal
    contribution_profit_krw: Decimal


class ConsolePartnerReceivableRow(BaseModel):
    receivable_id: str
    sale_id: str
    due_date: date
    original_amount_krw: Decimal
    received_amount_krw: Decimal
    outstanding_amount_krw: Decimal
    days_overdue: int | None
    aging_bucket: AgingBucket
    status: str


class ConsolePartnerDetailResponse(BaseModel):
    sim_run_id: str
    as_of: date
    basic: ConsolePartnerBasic
    summary: ConsolePartnerSummary
    recent_sales: list[ConsolePartnerSaleRow]
    item_summary: list[ConsolePartnerItemRow]
    receivables: list[ConsolePartnerReceivableRow]
    #: 🔴 Credit is Finance's number.  Sales does not compute it from receivables —
    #: a second formula here would quietly become a second credit policy.
    credit: None = None
    credit_status: str = "UNSUPPORTED"
