"""미지급 매입채무 취소의 판정 — 대상 정규화 · 잠근 행 검사 · 취소 금액 대조.

순서는 `service/cancellation.py`, SQL 은 `repository/cancellation.py`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.finance.schemas.cancellation import FinanceCancellationConflict


@dataclass(frozen=True)
class PayableFact:
    purchase_id: str
    sim_run_id: str
    original_amount_krw: Decimal
    paid_amount_krw: Decimal
    cancelled_amount_krw: Decimal
    outstanding_amount_krw: Decimal
    status: str


@dataclass(frozen=True)
class StateFact:
    finance_state_id: str
    financing_mode: str
    unsettled_purchase_payables_krw: Decimal


def normalize_purchase_ids(purchase_ids: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for purchase_id in purchase_ids:
        if not isinstance(purchase_id, str) or not purchase_id.strip():
            raise ValueError("purchase_ids must contain non-blank strings")
        if purchase_id not in seen:
            normalized.append(purchase_id)
            seen.add(purchase_id)
    if not normalized:
        raise ValueError("purchase_ids must not be empty")
    return tuple(normalized)


def payable_fact(row: Any) -> PayableFact:
    return PayableFact(
        purchase_id=str(_value(row, "purchase_id", 0)),
        sim_run_id=str(_value(row, "sim_run_id", 1)),
        original_amount_krw=Decimal(str(_value(row, "original_amount_krw", 2))),
        paid_amount_krw=Decimal(str(_value(row, "paid_amount_krw", 3))),
        cancelled_amount_krw=Decimal(str(_value(row, "cancelled_amount_krw", 4))),
        outstanding_amount_krw=Decimal(str(_value(row, "outstanding_amount_krw", 5))),
        status=str(_value(row, "status", 6)),
    )


def validate_complete_target_set(
    requested: tuple[str, ...], payables: tuple[PayableFact, ...]
) -> None:
    found = {row.purchase_id for row in payables}
    missing = tuple(purchase_id for purchase_id in requested if purchase_id not in found)
    if missing:
        raise FinanceCancellationConflict("payable_not_found", purchase_ids=missing)
    if len(found) != len(payables):
        raise FinanceCancellationConflict("payable_target_ambiguous")
    if len({row.sim_run_id for row in payables}) != 1:
        raise FinanceCancellationConflict("payable_runtime_axis_ambiguous")

    blocked: list[str] = []
    for row in payables:
        if row.status == "OPEN":
            if row.paid_amount_krw != 0 or row.cancelled_amount_krw != 0:
                blocked.append(row.purchase_id)
            continue
        if row.status == "CANCELLED":
            if (
                row.paid_amount_krw != 0
                or row.outstanding_amount_krw != 0
                or row.cancelled_amount_krw != row.original_amount_krw
            ):
                blocked.append(row.purchase_id)
            continue
        blocked.append(row.purchase_id)
    if blocked:
        raise FinanceCancellationConflict("payable_not_cancellable", purchase_ids=tuple(blocked))
    statuses = {row.status for row in payables}
    if statuses == {"OPEN", "CANCELLED"}:
        # This operation changes the complete locked set atomically, so its legitimate retry
        # states are all OPEN (not applied) or all CANCELLED (already applied). Without a
        # stable Master cancellation-event ID, a mixed set cannot be proven to be this
        # operation's partial retry and must not be silently completed.
        raise FinanceCancellationConflict("payable_cancellation_state_mixed")


def state_fact(rows: list, *, financing_mode: str) -> StateFact | None:
    """잠근 그날 상태 행에서 취소가 쓸 사실. 없으면 `None`, 둘이면 막는다."""
    if len(rows) > 1:
        raise FinanceCancellationConflict("finance_runtime_axis_ambiguous")
    if not rows:
        return None
    row = rows[0]
    state = StateFact(
        finance_state_id=str(_value(row, "finance_state_id", 0)),
        financing_mode=str(_value(row, "financing_mode", 1)),
        unsettled_purchase_payables_krw=Decimal(
            str(_value(row, "unsettled_purchase_payables_krw", 2))
        ),
    )
    if state.financing_mode != financing_mode:
        raise FinanceCancellationConflict("finance_runtime_axis_mismatch")
    return state


def cancelled_payables(rows: list) -> tuple[tuple[str, Decimal], ...]:
    """취소 UPDATE 가 돌려준 (매입 id, 취소 금액)."""
    return tuple(
        (
            str(_value(row, "purchase_id", 0)),
            Decimal(str(_value(row, "cancelled_amount_krw", 1))),
        )
        for row in rows
    )


def checked_cancelled_amount(
    changed: tuple[tuple[str, Decimal], ...],
    *,
    eligible_ids: tuple[str, ...],
    expected_amount: Decimal,
) -> Decimal:
    """잠그고 확인한 대상이 그대로 취소됐는지 보고 취소 금액 합을 돌려준다."""
    if {purchase_id for purchase_id, _ in changed} != set(eligible_ids):
        # Rows were locked and validated above. A mismatch is a ledger race or contract
        # violation, never a partial success; the caller must roll the transaction back.
        raise FinanceCancellationConflict("payable_cancellation_race")

    newly_cancelled_amount = sum((amount for _, amount in changed), start=Decimal(0))
    if newly_cancelled_amount != expected_amount:
        raise FinanceCancellationConflict("payable_cancellation_amount_mismatch")
    return newly_cancelled_amount


def returned_state_id(rows: list) -> str:
    """상태 갱신이 돌려준 행은 정확히 하나여야 한다 — 아니면 미지급이 모자랐다."""
    if len(rows) != 1:
        raise FinanceCancellationConflict("finance_unsettled_underflow")
    return str(_value(rows[0], "finance_state_id", 0))


def _value(row: Any, name: str, index: int) -> Any:
    if isinstance(row, Mapping):
        return row[name]
    return row[index]
