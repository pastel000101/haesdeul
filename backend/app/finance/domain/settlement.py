"""매입대금 지급 계산 — 채무별로 낼 돈 · 지급 뒤 상태.

순서는 `service/settlement.py`, SQL 은 `repository/settlement.py`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.finance.domain.values import money_amount
from app.finance.schemas.data_port import FinanceDataNotReady
from app.finance.schemas.settlement import PayableSettlement

_ZERO = Decimal(0)


def payable_settlement(row: Any) -> PayableSettlement | None:
    """잠근 채무 한 행에서 이번에 낼 돈. 낼 돈이 없으면 `None`."""
    payable_id = row["payable_id"]
    if not isinstance(payable_id, str) or not payable_id.strip():
        raise FinanceDataNotReady("payable_id")
    outstanding = money_amount(row["outstanding_amount_krw"], "payable_outstanding")
    recognized = money_amount(row["recognized_amount_krw"], "payable_recognized_amount")
    # 주의: 인식액이 남은 잔액보다 클 수 있다 — 인식 뒤에 다른 경로가 일부를 갚거나
    # 취소했으면 그렇다. 장부에 남은 만큼만 낸다. 넘겨 내면 음수 잔액이 선다.
    paid_now = min(recognized, outstanding)
    if paid_now <= 0:
        return None
    next_paid = money_amount(row["paid_amount_krw"], "payable_paid") + paid_now
    next_outstanding = outstanding - paid_now
    next_status = "SETTLED" if next_outstanding == 0 else "PARTIAL"
    return PayableSettlement(
        payable_id=payable_id,
        paid_krw=paid_now,
        next_paid_amount_krw=next_paid,
        next_outstanding_amount_krw=next_outstanding,
        next_status=next_status,
    )


def state_after_payment(rows: list, *, paid_krw: Decimal) -> tuple[object, Decimal, Decimal]:
    """지급 뒤 그날 상태의 (상태 id, 현금, 미지급 채무)."""
    if len(rows) != 1:
        # 축이 둘이면 어느 장부에서 돈이 나갔는지 고르지 않는다. 고르는 순간 무차입 장부의
        # 현금이 대출 장부의 지급으로 줄어들 수 있다.
        raise FinanceDataNotReady("finance_state_for_settlement")
    row = rows[0]
    current_cash = money_amount(row["current_cash_krw"], "current_cash_krw")
    unsettled = money_amount(
        row["unsettled_purchase_payables_krw"], "unsettled_purchase_payables_krw"
    )
    # 현금은 음수가 될 수 있다. 그것은 «돈이 모자랐다» 는 사실이고, 막으면 가장 위험한 날의
    # 장부가 사라진다.
    return row["finance_state_id"], current_cash - paid_krw, max(_ZERO, unsettled - paid_krw)
