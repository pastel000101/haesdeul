"""미지급 매입채무 취소 — 결과와 충돌.

판정은 `domain/cancellation.py`, 순서는 `service/cancellation.py`, SQL 은
`repository/cancellation.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal


class FinanceCancellationConflict(RuntimeError):
    """The requested reversal conflicts with persisted Finance ledger facts."""

    def __init__(self, reason: str, *, purchase_ids: Sequence[str] = ()) -> None:
        self.reason = reason
        self.purchase_ids = tuple(purchase_ids)
        super().__init__(reason)


@dataclass(frozen=True)
class FinanceCancellationResult:
    """Structured facts returned by one cancellation attempt."""

    requested_count: int
    newly_cancelled_count: int
    newly_cancelled_amount_krw: Decimal
    finance_state_updated: bool
    finance_state_id: str | None
