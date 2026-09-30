"""운영 콘솔 거래처 여신 응답.

★ 2026-09-29 재구성 BL-014: `finance/console_credit.py` 에서 응답 모델만 옮겼다(필드 그대로).
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ConsoleCreditCollection(BaseModel):
    """계약상 결제 예정 한 건과, **그 돈이 들어온다면** 남는 여신."""

    model_config = ConfigDict(extra="forbid")

    due_date: date
    amount_krw: Decimal
    #: 결제 예정일이 기준일보다 앞선다 — 이미 받았어야 할 돈이다.
    overdue: bool
    #: 이 건까지 예정대로 들어온다면 남는 여신. 한도가 없으면 `None` 이다.
    available_credit_after_krw: Decimal | None


class ConsolePartnerCredit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    partner_id: str
    partner_name: str | None
    #: 거래처 계약상 결제일수. 모르면 `None` 이다 (0일 결제와 다르다).
    payment_days: int | None
    #: 그날 유효한 여신한도. **`None` 은 한도가 정해지지 않았다는 뜻이고 0원이 아니다.**
    credit_limit_krw: Decimal | None
    credit_limit_evidence_grade: str | None
    current_ar_krw: Decimal
    overdue_ar_krw: Decimal
    open_receivable_count: int
    available_credit_krw: Decimal | None
    credit_utilization_rate: Decimal | None
    upcoming_collections: list[ConsoleCreditCollection]


class ConsoleCreditResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sim_run_id: str
    as_of: date
    partners: list[ConsolePartnerCredit]
