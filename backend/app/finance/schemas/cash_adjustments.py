"""사용자 기록 자금 입·출금 — 요청 · 결과 · 충돌.

요청 모델(`CashAdjustmentChange`)은 화면 라우터와 마스터 ask 가 같이 import 한다.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

CashDirection = Literal["INFLOW", "OUTFLOW"]
CashCategory = Literal["OWNER_INJECTION", "OWNER_WITHDRAWAL", "OTHER"]


class CashAdjustmentConflict(ValueError):
    """자금 조정이 현재 Finance State 불변식을 위반했다."""


@dataclass(frozen=True)
class CashAdjustmentResult:
    cash_adjustment_id: str
    current_cash_krw: Decimal


#: 화면 `POST /finance/cash-adjustments` 와 마스터 ask(FINANCE_CASH_ADJUSTMENT_CREATE)가
#: 같이 쓰는 요청.
class CashAdjustmentChange(BaseModel):
    sim_run_id: str = Field(min_length=1)
    financing_mode: str = Field(min_length=1)
    adjustment_date: date
    direction: Literal["INFLOW", "OUTFLOW"]
    category: Literal["OWNER_INJECTION", "OWNER_WITHDRAWAL", "OTHER"]
    amount_krw: Decimal = Field(gt=0)
    source_ref: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)
    ]
    recorded_by: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    note: str | None = Field(default=None, max_length=1000)
