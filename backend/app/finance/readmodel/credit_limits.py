"""거래처 여신한도 기간 이력 조회 (`GET /finance/credit-limits`).

연결은 HTTP 입구가 `Depends` 로 빌린 요청 연결을 인자로 받는다. 여기서는 읽기만 한다 — 두 SELECT
를 감싸는 트랜잭션 블록(정상 commit · 예외 rollback)은 `service/credit_limits.py` 의
`read_credit_limit_history` 가 연다.
"""

from datetime import date
from typing import Any

from app.finance.repository.credit_limits import partner_exists, select_credit_limit_history
from app.finance.schemas.credit_limits import CreditLimitHistoryItem


def credit_limit_history(
    conn: Any, *, partner_id: str, as_of: date
) -> list[CreditLimitHistoryItem]:
    """한 거래처의 여신한도 이력 — 최신 적용일부터. 없는 거래처는 `LookupError`."""
    if not partner_exists(conn, partner_id=partner_id):
        raise LookupError("거래처를 찾지 못했습니다.")
    return [
        CreditLimitHistoryItem.model_validate(row)
        for row in select_credit_limit_history(conn, partner_id=partner_id, as_of=as_of)
    ]
