"""거래처 채권 · 여신한도 조회 (판정 경로). **조회 실패는 «없음» 이 아니다** — 연결을 빌리다 난
실패까지 `FinanceDataNotReady` 로 세운다.

★ 2026-09-29 재구성 BL-014: `finance/db.py` 에서 옮겼다.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from app.core import db as core_db
from app.finance.domain.partner_credit import credit_limit_from_rows, partner_receivables_from_rows
from app.finance.repository.partner_credit import (
    select_active_credit_limits,
    select_partner_receivables,
)
from app.finance.schemas.data_port import FinanceDataNotReady
from app.finance.schemas.sales_validation import PartnerReceivable


def load_partner_receivables(
    *, sim_run_id: str, as_of: date, partner_id: str
) -> list[PartnerReceivable]:
    """거래처 채권 원장을 Finance 사실로 옮긴다.

    ★ 상태를 여기서 거르지 않는다. `COLLECTED` · `WRITEOFF` 를 빼는 것은 집계의
      판단이고, 그 판단은 한 곳에만 있어야 한다.

    ★ `receivable_id` 를 그대로 `source_ref` 로 쓴다 — 이미 `load_receivables` 가
      같은 값을 `ref_id` 로 쓰고 있다. 따라가면 실제 원장 행에 닿는다.
    """
    if not partner_id.strip():
        raise ValueError("partner_id must not be blank")
    try:
        with core_db.read_connection() as conn:
            rows = select_partner_receivables(
                conn, sim_run_id=sim_run_id, as_of=as_of, partner_id=partner_id
            )
    except Exception as exc:  # 조회 실패는 "채권 없음" 이 아니다 — 세운다.
        raise FinanceDataNotReady("partner_receivables") from exc
    return partner_receivables_from_rows(rows)


def load_partner_credit_limit(*, as_of: date, partner_id: str) -> Decimal | None:
    """그날 유효한 **거래처 여신한도**. 없으면 `None`.

    ★ **한도는 정책이 아니라 거래처가 소유한 사실**이다. 그래서 Finance Policy 가
      아니라 `partner_credit_limits` 에서 읽는다 — 실행마다 같은 값이 아니고 계약마다
      다르다.

    🔴 **`0` 과 `None` 은 다르다.**

      ```text
      Decimal(0)  여신한도가 0원이라는 사실   → 판정한다 (어떤 제안도 한도를 넘는다)
      None        한도가 아직 확정되지 않음   → 판정하지 않는다 (RUNTIME_NOT_READY)
      ```

      그래서 행이 없을 때만 `None` 이다. 금액 컬럼은 `NOT NULL` 이라 "0 인지 모르는
      지" 가 한 칸에 섞이지 않는다.

    🔴 **겹치는 활성 구간이 있으면 고르지 않는다.** 어느 한도로 판정했는지 되짚을 수
      없는 상태에서 하나를 집으면, 그 사고는 에러 없이 숫자만 바꾼다. 기간 겹침을
      DB 제약으로 막지 않았으므로(`btree_gist` 를 공유 스키마에 더하지 않았다)
      여기서 fail-closed 한다.

    ★ **미래 구간을 읽지 않는다.** `effective_from <= as_of` 이고, 끝이 있으면
      `as_of <= effective_to` 인 행만 그날의 사실이다.
    """
    if not partner_id.strip():
        raise ValueError("partner_id must not be blank")
    try:
        with core_db.read_connection() as conn:
            rows = select_active_credit_limits(conn, partner_id=partner_id, as_of=as_of)
    except Exception as exc:  # 조회 실패는 "한도 없음" 이 아니다 — 세운다.
        raise FinanceDataNotReady("partner_credit_limit") from exc
    return credit_limit_from_rows(rows)


def partner_credit_limit_on(conn: Any, *, as_of: date, partner_id: str) -> Decimal | None:
    """`load_partner_credit_limit` 과 같은 규칙을 **이미 빌린 조회 연결**로 읽는다.

    ★ 화면 여신 현황(`readmodel/console_credit.py`)이 거래처마다 연결을 새로 빌리지 않고
      한 연결로 읽을 때 쓴다. 빈 거래처 · 조회 실패 · 행 규칙(`credit_limit_from_rows`)은
      위와 같고, 다른 것은 연결을 빌리는 자리가 여기 없다는 것뿐이다.
    """
    if not partner_id.strip():
        raise ValueError("partner_id must not be blank")
    try:
        rows = select_active_credit_limits(conn, partner_id=partner_id, as_of=as_of)
    except Exception as exc:  # 조회 실패는 "한도 없음" 이 아니다 — 세운다.
        raise FinanceDataNotReady("partner_credit_limit") from exc
    return credit_limit_from_rows(rows)
