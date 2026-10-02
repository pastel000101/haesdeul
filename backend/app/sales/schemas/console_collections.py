"""판매 운영 콘솔의 수금 응답 — `readmodel/console_collections.py` 가 채운다."""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from app.contracts.aging import AgingBucket

_ZERO = Decimal(0)


class ConsoleCollectionRow(BaseModel):
    partner_id: str | None
    partner_name: str | None
    sale_id: str
    receivable_id: str
    original_amount_krw: Decimal
    received_amount_krw: Decimal
    outstanding_amount_krw: Decimal
    due_date: date
    #: Null once nothing is outstanding — a settled receivable is not "0 days late".
    days_overdue: int | None
    aging_bucket: AgingBucket
    status: str


class ConsoleCollectionSummary(BaseModel):
    total_outstanding_krw: Decimal = _ZERO
    overdue_krw: Decimal = _ZERO
    collected_krw: Decimal = _ZERO


class ConsoleCollectionsResponse(BaseModel):
    sim_run_id: str
    as_of: date
    summary: ConsoleCollectionSummary
    rows: list[ConsoleCollectionRow]
