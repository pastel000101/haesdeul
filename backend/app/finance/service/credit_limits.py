"""거래처 여신한도 등록 — 화면 `POST /finance/credit-limits` 와 마스터 ask 가 같은 함수를 부른다.

★ 한 요청 = 한 트랜잭션. 받은 연결에 트랜잭션을 연다 — 화면은 `Depends` 로 빌린 요청 연결, ask 는
  `core_db.connection()` 으로 빌린 연결을 넘긴다(종전과 같은 대여). 정상이면 commit, 예외면 rollback
  (`core_db.transaction`).

★ 받지 않은 요청은 `FinanceWriteRejected` 하나로 낸다 — 없는 거래처는 NOT_FOUND, 기간 충돌은
  CONFLICT. 상태 코드와 사용자 문장으로 옮기는 것은 부르는 쪽이다.

★ 2026-09-29 재구성 BL-014: `finance/router.py` 의 `register_credit_limit` 핸들러 몸통을 옮겼다
  (순서 · 문장 · 저장 그대로).
"""

from uuid import uuid4

from app.core import db as core_db
from app.finance.domain.credit_limits import credit_limit_period_to_close
from app.finance.repository.credit_limits import (
    close_credit_limit,
    insert_credit_limit,
    lock_active_credit_limits,
    partner_exists,
)
from app.finance.schemas.credit_limits import CreditLimitChange
from app.finance.schemas.write_rejection import FinanceWriteRejected


def change_credit_limit(
    conn: core_db.Connection, change: CreditLimitChange
) -> dict[str, object]:
    """열린 한도 기간을 끝내고 새 기간을 추가한다; 과거 금액은 덮어쓰지 않는다."""
    try:
        with core_db.transaction(conn):
            if not partner_exists(conn, partner_id=change.partner_id):
                raise LookupError("거래처를 찾지 못했습니다.")
            current = credit_limit_period_to_close(
                lock_active_credit_limits(conn, partner_id=change.partner_id),
                effective_from=change.effective_from,
            )
            if current is not None:
                close_credit_limit(
                    conn,
                    partner_credit_limit_id=current["partner_credit_limit_id"],
                    effective_from=change.effective_from,
                )
            credit_id = f"CREDIT-{uuid4()}"
            insert_credit_limit(conn, credit_id=credit_id, change=change)
        return {
            "partner_credit_limit_id": credit_id,
            "partner_id": change.partner_id,
            "effective_from": change.effective_from,
            "credit_limit_krw": change.credit_limit_krw,
        }
    except ValueError as error:
        raise FinanceWriteRejected("CONFLICT", str(error)) from error
    except LookupError as error:
        raise FinanceWriteRejected("NOT_FOUND", str(error)) from error
