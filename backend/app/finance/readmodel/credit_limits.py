"""거래처 여신한도 기간 이력 조회 (`GET /finance/credit-limits`).

★ 2026-09-29 재구성 BL-014: `finance/router.py` 핸들러 안의 조회를 옮겼다. 연결은 HTTP 입구가
  `Depends` 로 빌린 요청 연결을 **인자로 받는다**(종전과 같은 대여 시점). 읽기만 하고 commit 하지
  않는다 — 종전 핸들러는 두 SELECT 를 트랜잭션 블록으로 감싸 commit 했고, 지금은 연결을 돌려줄 때
  풀이 끝나지 않은 읽기 트랜잭션을 rollback 한다. 읽은 값은 같다.
"""

from datetime import date
from typing import Any

from app.finance.repository.credit_limits import partner_exists, select_credit_limit_history
from app.finance.schemas.credit_limits import CreditLimitHistoryItem


def credit_limit_history(
    conn: Any, *, partner_id: str, as_of: date
) -> list[CreditLimitHistoryItem]:
    """한 거래처의 여신한도 이력 — 최신 적용일부터. **없는 거래처는 `LookupError`.**"""
    if not partner_exists(conn, partner_id=partner_id):
        raise LookupError("거래처를 찾지 못했습니다.")
    return [
        CreditLimitHistoryItem.model_validate(row)
        for row in select_credit_limit_history(conn, partner_id=partner_id, as_of=as_of)
    ]
