"""운영 콘솔 매출채권 응답.

★ 2026-09-29 재구성 BL-014: `finance/console_receivables.py` 에서 응답 모델만 옮겼다(필드 그대로).
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from app.contracts.aging import AgingBucket


class ConsoleReceivableRow(BaseModel):
    receivable_id: str
    sale_id: str
    partner_id: str | None
    partner_name: str | None
    original_amount_krw: Decimal
    received_amount_krw: Decimal
    outstanding_amount_krw: Decimal
    due_date: date
    days_overdue: int | None
    aging_bucket: AgingBucket
    status: str


class ConsoleReceivableSummary(BaseModel):
    current_krw: Decimal = Decimal(0)
    days_1_7_krw: Decimal = Decimal(0)
    days_8_30_krw: Decimal = Decimal(0)
    days_30_plus_krw: Decimal = Decimal(0)
    total_outstanding_krw: Decimal = Decimal(0)


class ConsoleReceivablesResponse(BaseModel):
    sim_run_id: str
    as_of: date
    summary: ConsoleReceivableSummary
    rows: list[ConsoleReceivableRow]
