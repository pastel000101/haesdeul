"""사용자 수금 기록 — `POST /finance/receivables/collections`.

HTTP 만: 요청 → `finance/service/collections.py` → 응답, 업무 거절 → 상태 코드. 마스터 ask 도 같은
service 를 부른다.
"""

from fastapi import APIRouter, status

from app.api.finance.deps import DbConnection, rejected
from app.finance.schemas.collections import ReceivableCollectionChange
from app.finance.schemas.write_rejection import FinanceWriteRejected
from app.finance.service import collections as collection_service

router = APIRouter(prefix="/finance", tags=["finance"])


@router.post("/receivables/collections", status_code=status.HTTP_201_CREATED)
def record_receivable_collection(
    change: ReceivableCollectionChange, conn: DbConnection
) -> dict[str, object]:
    """한 채권의 실제 전액/부분 수금을 누적 전이로 기록한다."""
    try:
        return collection_service.record_collection(conn, change)
    except FinanceWriteRejected as error:
        raise rejected(error) from error
