"""거래처 여신한도 — 등록 요청과 기간 이력.

★ 2026-09-29 재구성 BL-014: `finance/router.py` 에서 옮겼다(필드 · 검증 그대로). 화면
  `POST /finance/credit-limits` 와 마스터 ask(FINANCE_CREDIT_LIMIT_UPSERT)가 같은 요청 모델을
  import 한다. 등록 순서 · 트랜잭션은 `service/credit_limits.py`, 기간 판정은
  `domain/credit_limits.py`, SQL 은 `repository/credit_limits.py`, 이력 조회는
  `readmodel/credit_limits.py`.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints


class CreditLimitChange(BaseModel):
    """사용자가 등록하는 거래처 여신한도 이력 한 건."""

    partner_id: str = Field(min_length=1)
    credit_limit_krw: Decimal = Field(ge=0)
    effective_from: date
    evidence_grade: str = Field(pattern="^(OFFICIAL|VENDOR|SIM_FIXED)$")
    source_ref: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)
    ]
    recorded_by: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    note: str | None = Field(default=None, max_length=1000)


class CreditLimitHistoryItem(BaseModel):
    """거래처 여신한도 원장의 기간 이력 한 건."""

    partner_credit_limit_id: str
    partner_id: str
    credit_limit_krw: Decimal
    effective_from: date
    effective_to: date | None
    evidence_grade: str
    source_ref: str
    recorded_by: str
    policy_version: str
    usage_scope: str
    note: str | None
    is_active: bool
    is_current: bool
