"""판매 확정분 → 재무 매출채권 — 쓰기 계획 · 결과 · 충돌."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


class ReceivablePersistenceConflict(RuntimeError):
    """같은 sale_id 축으로 다른 사실이 들어왔다."""


@dataclass(frozen=True)
class ReceivableWritePlan:
    receivable_id: str
    sale_id: str
    sim_run_id: str
    financing_mode: str
    finance_state_id: str
    issued_date: date
    due_date: date
    original_amount_krw: Decimal
    received_amount_krw: Decimal
    outstanding_amount_krw: Decimal
    status: str


@dataclass(frozen=True)
class ReceivableWriteResult:
    receivable_id: str
    finance_state_id: str
    receivables_written: int
    finance_state_updates: int
