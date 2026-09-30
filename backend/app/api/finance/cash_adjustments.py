"""자금 조정 — `POST /finance/cash-adjustments`.

HTTP 만: 요청 → `finance/service/cash_adjustments.py` → 응답, 업무 거절 → 상태 코드. 마스터 ask 도
같은 service 를 부른다.

★ 2026-09-30 재구성 BL-019: `app/finance/router.py` 에서 옮겼다 — 핸들러 이름 · docstring(OpenAPI
  설명) · URL · 상태 코드 · 문구 그대로.
"""

from fastapi import APIRouter, status

from app.api.finance.deps import DbConnection, rejected
from app.finance.schemas.cash_adjustments import CashAdjustmentChange
from app.finance.schemas.write_rejection import FinanceWriteRejected
from app.finance.service import cash_adjustments as cash_adjustment_service

router = APIRouter(prefix="/finance", tags=["finance"])


@router.post("/cash-adjustments", status_code=status.HTTP_201_CREATED)
def create_cash_adjustment(change: CashAdjustmentChange, conn: DbConnection) -> dict[str, object]:
    """사용자 자금 입금·출금을 근거와 함께 기록한다."""
    try:
        return cash_adjustment_service.apply_cash_adjustment(conn, change)
    except FinanceWriteRejected as error:
        raise rejected(error) from error
