"""여신한도 — `GET · POST /finance/credit-limits`.

HTTP 만: 요청 → `finance/service/credit_limits.py` → 응답, 업무 거절 → 상태 코드. 마스터 ask 도
같은 service 를 부른다.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.finance.deps import DbConnection, rejected
from app.finance.schemas.credit_limits import CreditLimitChange, CreditLimitHistoryItem
from app.finance.schemas.write_rejection import FinanceWriteRejected
from app.finance.service import credit_limits as credit_limit_service

router = APIRouter(prefix="/finance", tags=["finance"])


@router.get("/credit-limits", response_model=list[CreditLimitHistoryItem])
def get_credit_limits(
    partner_id: Annotated[str, Query(min_length=1)],
    as_of: date,
    conn: DbConnection,
) -> list[CreditLimitHistoryItem]:
    """한 거래처의 여신한도 이력을 최신 적용일부터 반환한다."""
    try:
        return credit_limit_service.read_credit_limit_history(
            conn, partner_id=partner_id, as_of=as_of
        )
    except LookupError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error


@router.post("/credit-limits", status_code=status.HTTP_201_CREATED)
def register_credit_limit(change: CreditLimitChange, conn: DbConnection) -> dict[str, object]:
    """열린 한도 기간을 끝내고 새 기간을 추가한다; 과거 금액은 덮어쓰지 않는다."""
    try:
        return credit_limit_service.change_credit_limit(conn, change)
    except FinanceWriteRejected as error:
        raise rejected(error) from error
