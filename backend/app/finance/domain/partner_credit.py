"""거래처 채권 · 여신한도 행 → 재무 사실. **못 읽은 것을 «없음» 으로 바꾸지 않는다.**

★ 2026-09-29 재구성 BL-014: `finance/db.py` 에서 옮겼다(몸통 그대로). SQL 은
  `repository/partner_credit.py`, 조회
  연결과 조회 실패 처리는 `readmodel/partner_credit.py`.
"""

from datetime import date
from decimal import Decimal
from typing import Any, cast

from app.finance.schemas.data_port import FinanceDataNotReady
from app.finance.schemas.sales_validation import PartnerReceivable


def partner_receivables_from_rows(rows: list[dict[str, object]]) -> list[PartnerReceivable]:
    """채권 행을 `PartnerReceivable` 로 옮긴다. **못 읽은 금액은 못 읽은 것이다.**"""
    receivables: list[PartnerReceivable] = []
    for row in rows:
        amount = row.get("outstanding_amount_krw")
        if isinstance(amount, bool) or not isinstance(amount, Decimal):
            # 🔴 float 로 바꾸지 않는다. 못 읽은 금액은 못 읽은 것이다.
            raise FinanceDataNotReady("partner_receivables")
        try:
            receivables.append(
                PartnerReceivable(
                    receivable_id=str(row["receivable_id"]),
                    due_date=cast(date, row["due_date"]),
                    outstanding_amount_krw=amount,
                    status=cast(Any, row["status"]),
                    source_ref=str(row["receivable_id"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise FinanceDataNotReady("partner_receivables") from exc
    return receivables


def credit_limit_from_rows(rows: list[dict[str, object]]) -> Decimal | None:
    """유효 구간 행에서 그날 한도. **없으면 `None`, 겹치면 막는다.**"""
    if not rows:
        # ★ 없는 것은 없는 것이다. 0 으로도 무한대로도 바꾸지 않는다.
        return None
    if len(rows) > 1:
        raise FinanceDataNotReady("partner_credit_limit_ambiguous")
    amount = rows[0].get("credit_limit_krw")
    if isinstance(amount, bool) or not isinstance(amount, Decimal):
        # 🔴 float 로 바꾸지 않는다. 못 읽은 금액은 못 읽은 것이다.
        raise FinanceDataNotReady("partner_credit_limit")
    if not amount.is_finite() or amount < 0:
        raise FinanceDataNotReady("partner_credit_limit")
    return amount
