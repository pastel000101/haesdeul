"""운영 콘솔 운영비 응답.

★ 2026-09-29 재구성 BL-014: `finance/console_expenses.py` 에서 응답 모델만 옮겼다(필드 그대로).
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class ConsoleExpenseRow(BaseModel):
    expense_id: str
    expense_date: date
    #: What the ledger stores.  Grouping and filtering use this, never the label.
    raw_category: str
    #: What the screen shows.  Falls back to the stored value when unnamed.
    display_category: str
    amount_krw: Decimal
    #: Null means the stored row carries no evidence reference.
    source_ref: str | None
    note: str | None
    #: 원장이 적어 둔 생명주기 상태. `ACCRUED` · `PAID` · `CANCELLED`.
    status: str = "PAID"
    #: 지급하기로 한 날. 이 칸이 생기기 전 행은 `None` 이다.
    due_date: date | None = None
    #: 🔴 **실제로 지급한 날. 모르면 `None` 이고, 발생일로 메우지 않는다.**
    #:
    #:   마감은 지급일을 모르는 기존 `PAID` 행에 한해 `expense_date` 를 지급 기준일로
    #:   읽지만(읽기 전용 호환), **화면은 그러면 안 된다.** 추측한 날짜를 «지급일» 이라고
    #:   적으면 사용자는 그날 돈이 나간 것으로 읽고, 통장과 맞춰 보다 원인을 못 찾는다.
    paid_date: date | None = None
    #: 지급일을 아는가. `PAID` 인데 거짓이면 «지급일 미상» 인 기존 데이터다.
    paid_date_known: bool = False
    #: 납품에 붙은 비용이면 그 납품 번호. 마감에서 물류비 칸으로 가는 근거다.
    related_delivery_id: str | None = None


class ConsoleExpenseCategoryTotal(BaseModel):
    raw_category: str
    display_category: str
    expense_count: int
    total_amount_krw: Decimal


class ConsoleExpenseSummary(BaseModel):
    total_expenses_krw: Decimal = Decimal(0)
    #: 아직 안 나간 돈. `ACCRUED` 합계다.
    accrued_krw: Decimal = Decimal(0)
    #: 실제로 나간 돈. `PAID` 합계다.
    paid_krw: Decimal = Decimal(0)
    #: 나가지 않기로 한 돈. `CANCELLED` 합계이고 **현금과 무관하다.**
    cancelled_krw: Decimal = Decimal(0)
    accrued_count: int = 0
    category_totals: list[ConsoleExpenseCategoryTotal] = []


class ConsoleExpensesResponse(BaseModel):
    sim_run_id: str
    as_of: date
    summary: ConsoleExpenseSummary
    rows: list[ConsoleExpenseRow]
