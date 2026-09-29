"""판매 추이 응답 — 날짜별로 접은 판매 사실. `readmodel/console_trend.py` 가 채운다.

★ 2026-09-29 BL-013: `sales/console_trend.py` 에서 옮겼다. `MAX_TREND_DAYS` 는 화면 라우트의
  쿼리 상한과 조회가 함께 쓰는 값이라 응답 모델과 같은 자리에 둔다.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

#: 한 번에 돌려주는 최대 일수. 판매 실행이 분기 단위라 한 분기를 덮는다.
MAX_TREND_DAYS = 400


class SalesTrendPoint(BaseModel):
    """하루치 판매. **없는 날은 행이 없다** — 0 으로 채우지 않는다."""

    model_config = ConfigDict(extra="forbid")

    sale_date: date
    sales_count: int
    quantity_kg: Decimal
    sales_amount_krw: Decimal
    contribution_profit_krw: Decimal


class SalesTrendResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sim_run_id: str
    as_of: date
    rows: list[SalesTrendPoint]
