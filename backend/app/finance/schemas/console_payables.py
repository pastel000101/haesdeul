"""운영 콘솔 매입채무 응답.

★ 2026-09-29 재구성 BL-014: `finance/console_payables.py` 에서 응답 모델만 옮겼다(필드 그대로).
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class ConsolePayableRow(BaseModel):
    payable_id: str
    #: The purchase this debt came from.  Null means the stored row has no source
    #: reference — the console reports that absence instead of inventing one.
    purchase_id: str | None
    issued_date: date
    due_date: date
    original_amount_krw: Decimal
    paid_amount_krw: Decimal
    outstanding_amount_krw: Decimal
    #: Negative means the due date has already passed.  Zero means it is due today,
    #: which is not the same fact, so neither is folded into the other.
    days_until_due: int
    status: str


class ConsolePayableSummary(BaseModel):
    total_outstanding_krw: Decimal = Decimal(0)
    due_today_krw: Decimal = Decimal(0)
    due_next_7d_krw: Decimal = Decimal(0)
    overdue_krw: Decimal = Decimal(0)


class ConsolePayablesResponse(BaseModel):
    sim_run_id: str
    as_of: date
    summary: ConsolePayableSummary
    rows: list[ConsolePayableRow]
