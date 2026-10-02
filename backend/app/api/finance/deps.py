"""재무 HTTP 입구가 함께 쓰는 것 — 요청마다 빌린 연결, 업무 거절 → 상태 코드.

쓰는 곳은 `api/finance/` 의 자원별 라우트 파일이다 — 쓰기 service 가 트랜잭션을 열고, 라우트는
연결을 넘기고 거절을 상태 코드로 바꾸기만 한다.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.core import db as core_db
from app.finance.schemas.write_rejection import FinanceWriteRejected

#: 요청 처리 동안 공통 풀에서 빌린 연결. commit 하지 않는다 — 쓰기 트랜잭션은 service 가
#: `core_db.transaction(conn)` 으로 연다(정상 commit · 예외 rollback). `scope="function"` 이라
#: 응답을 보내기 전에 연결을 돌려준다. 주의: `/agent` 처럼 LLM 을 기다리는 라우트에는 걸지
#: 않는다.
DbConnection = Annotated[core_db.Connection, Depends(core_db.db_connection, scope="function")]

#: 재무 쓰기 service 가 받지 않은 요청 → 상태 코드.
_REJECTION_STATUS = {
    "NOT_FOUND": status.HTTP_404_NOT_FOUND,
    "CONFLICT": status.HTTP_409_CONFLICT,
    "INVALID": 422,
}


def rejected(error: FinanceWriteRejected) -> HTTPException:
    return HTTPException(status_code=_REJECTION_STATUS[error.reason], detail=error.message)
