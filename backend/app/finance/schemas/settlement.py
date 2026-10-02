"""매입대금 실제 지급 — 채무별 지급 · 결과 · 충돌."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

_ZERO = Decimal(0)


@dataclass(frozen=True)
class PayableSettlement:
    """채무 한 건에서 실제로 나간 돈."""

    payable_id: str
    paid_krw: Decimal
    next_paid_amount_krw: Decimal
    next_outstanding_amount_krw: Decimal
    next_status: str


@dataclass(frozen=True)
class SettlementResult:
    """이 마감에서 나간 돈 전부."""

    settled: tuple[PayableSettlement, ...]

    @property
    def total_paid_krw(self) -> Decimal:
        return sum((row.paid_krw for row in self.settled), start=_ZERO)


class FinanceSettlementConflict(RuntimeError):
    """잠근 행이 한 행이 아니었다. 조용히 넘어가지 않는다."""
