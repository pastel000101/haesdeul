"""자금 조정의 판정 — 금액 > 0, 출금 뒤 현금 ≥ 0.

★ 2026-09-29 재구성 BL-014: `finance/cash_adjustments.py` 를 판정 · 순서 · SQL 로 나눴다.
"""

from decimal import Decimal

from app.finance.schemas.cash_adjustments import CashAdjustmentConflict, CashDirection


def cash_adjustment_delta(*, direction: CashDirection, amount_krw: Decimal) -> Decimal:
    """입금은 더하고 출금은 뺀다. **0원 이하 조정은 받지 않는다.**"""
    if amount_krw <= 0:
        raise CashAdjustmentConflict("자금 조정 금액은 0원보다 커야 합니다.")
    return amount_krw if direction == "INFLOW" else -amount_krw


def cash_after_adjustment(current_cash_krw: object, delta: Decimal) -> Decimal:
    """조정 뒤 현금. **출금 뒤 현금이 0원보다 작아질 수 없다.**"""
    next_cash = Decimal(str(current_cash_krw)) + delta
    if next_cash < 0:
        raise CashAdjustmentConflict("출금 후 현금이 0원보다 작아질 수 없습니다.")
    return next_cash
