"""일반 운영비 — 상태 · 분류 어휘 · 결과 · 충돌 · 요청.

★ 2026-09-29 재구성 BL-014: 어휘 · 결과 · 충돌은 `finance/expenses.py`, 요청 모델(`ExpenseCreate` ·
  `ExpenseSettle`
  · `ExpenseCancel`)은 `finance/router.py` 에서 옮겼다. 분류 이름의 주인은 여기 하나다 — 쓰기 검사
  (`domain/expenses.py`), 마감 칸 나누기(`domain/closing.py`), 분류 목록 라우트가 같은 이름을 본다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

ExpenseStatus = Literal["ACCRUED", "PAID", "CANCELLED"]

#: 급여·이자 칸으로 가는 분류. **원장이 실제로 쓰는 이름이다.**
#:
#: ★ `INTEREST` 와 `LOAN_INTEREST` 가 둘 다 있다. 원장이 쓰는 이름은 `LOAN_INTEREST`
#:   이지만, 아는 이름을 지우면 예전 데이터가 다시 막힌다.
PAYROLL_INTEREST_CATEGORIES: frozenset[str] = frozenset(
    {"PAYROLL", "INTEREST", "LOAN_INTEREST"}
)

#: 일반 운영비 칸으로 가는 분류. 매입대금도 물류비도 급여·이자도 아닌 잔여다.
#:
#: 🔴 **`LABOR` 를 넣지 않았다.** 화면 이름표는 이것을 «인건비» 로 부르고 `PAYROLL` 을
#:   «급여» 로 부르는데, 둘이 같은 칸에 가야 하는지 다른 칸에 가야 하는지를 정한 문서가
#:   저장소에 없다. 모르는 것을 운영비로 밀어 넣으면 급여가 운영비로 적힌 날이 생긴다 —
#:   막히는 편이 낫다 (마감이 `daily_closing_expense_category` 로 선다).
#:
#: 🔴 **`LOGISTICS` · `TRANSPORT` · `LOGISTICS_SERVICE` 도 넣지 않았다.** 물류비는
#:   `related_delivery_id` 가 붙어 물류 칸으로 간다. 납품이 안 붙은 물류 분류는 어느
#:   칸에 가야 하는지가 정해진 적 없다.
OPERATING_EXPENSE_CATEGORIES: frozenset[str] = frozenset(
    {"RENT", "UTILITY", "COMMISSION", "PACKAGING", "DISPOSAL", "OTHER"}
)

#: 새 비용을 만들 때 받을 수 있는 분류 전부.
#:
#: ★ 조회는 모르는 분류도 그대로 보여 준다(`console_expenses`). **쓰기만 잠근다** —
#:   읽는 쪽이 과거 데이터를 거부하면 이미 적힌 사실이 화면에서 사라진다.
KNOWN_EXPENSE_CATEGORIES: frozenset[str] = (
    OPERATING_EXPENSE_CATEGORIES | PAYROLL_INTEREST_CATEGORIES
)


class ExpenseConflict(ValueError):
    """비용 원장의 현재 사실과 어긋나는 요청."""


@dataclass(frozen=True)
class ExpenseSettlement:
    """지급 한 번의 결과. **현금이 얼마가 됐는지까지 같이 돌려준다.**"""

    expense_id: str
    paid_date: date
    amount_krw: Decimal
    current_cash_krw: Decimal


#: 화면 `/finance/expenses*` 와 마스터 ask(FINANCE_EXPENSE_*)가 같이 쓰는 요청.
class ExpenseCreate(BaseModel):
    """새 운영비 한 건. **적는 순간의 상태는 언제나 `ACCRUED` 다.**

    🔴 **지급 여부를 사용자가 고르지 않는다.** 고를 수 있게 하면 «적으면서 바로 지급» 이
       생기고, 그 경로는 현금 차감을 건너뛴다. 지급은 지급 요청으로만 일어난다.
    """

    sim_run_id: str = Field(min_length=1)
    expense_date: date
    #: 지급하기로 한 날. 미래 현금유출 투영이 이 날짜로 이 돈을 센다.
    due_date: date
    expense_category: str = Field(min_length=1)
    amount_krw: Decimal = Field(gt=0)
    #: 비용 원장의 근거 정본. 다른 원장의 `source_ref` 와 이름이 다르다.
    evidence_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)
    ]
    related_delivery_id: str | None = Field(default=None, max_length=120)
    is_fixed: bool = False
    note: str | None = Field(default=None, max_length=1000)


class ExpenseSettle(BaseModel):
    """지급 한 번. 이 요청만이 현금을 줄인다."""

    sim_run_id: str = Field(min_length=1)
    financing_mode: str = Field(min_length=1)
    paid_date: date


class ExpenseCancel(BaseModel):
    """«나가지 않기로 한다». **현금은 변하지 않는다.**"""

    sim_run_id: str = Field(min_length=1)
