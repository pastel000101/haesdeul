"""미지급 매입채무 취소 — 마스터 취소 경로가 넘긴 연결로. **commit 하지 않는다.**

★ 2026-09-29 재구성 BL-014: `finance/cancellation.py` 를 판정 · 순서 · SQL 로 나눴다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from app.finance.domain.cancellation import (
    cancelled_payables,
    checked_cancelled_amount,
    normalize_purchase_ids,
    payable_fact,
    returned_state_id,
    state_fact,
    validate_complete_target_set,
)
from app.finance.domain.state_identity import daily_finance_state_id
from app.finance.repository.cancellation import (
    cancel_payables,
    carry_and_subtract_state,
    lock_cancellation_state,
    lock_payables,
    subtract_existing_state,
)
from app.finance.schemas.cancellation import FinanceCancellationConflict, FinanceCancellationResult
from app.finance.schemas.data_port import FinanceDataNotReady
from app.finance.service.inventory import load_inventory_snapshot_as_of


def cancel_finance_payables(
    conn: Any,
    *,
    purchase_ids: Sequence[str],
    as_of: date,
    target_state_date: date,
    financing_mode: str,
) -> FinanceCancellationResult:
    """Cancel unpaid Payables and reverse their daily-state obligation exactly once.

    The operation deliberately has no ``build`` phase: eligibility and the reversible amount
    are persisted Payable facts and must be read under row locks. It opens no connection and
    never commits, rolls back, or closes the supplied connection.
    """
    requested = normalize_purchase_ids(purchase_ids)
    if target_state_date <= as_of:
        raise ValueError("target_state_date must be after the cancellation as_of")

    payables = tuple(
        payable_fact(row) for row in lock_payables(conn, purchase_ids=list(requested))
    )
    validate_complete_target_set(requested, payables)

    eligible = tuple(row for row in payables if row.status == "OPEN")
    if not eligible:
        return FinanceCancellationResult(
            requested_count=len(requested),
            newly_cancelled_count=0,
            newly_cancelled_amount_krw=Decimal(0),
            finance_state_updated=False,
            finance_state_id=None,
        )

    sim_run_id = payables[0].sim_run_id
    target_state = state_fact(
        lock_cancellation_state(
            conn, sim_run_id=sim_run_id, financing_mode=financing_mode,
            state_date=target_state_date,
        ),
        financing_mode=financing_mode,
    )
    source_state = None
    if target_state is None:
        source_state = state_fact(
            lock_cancellation_state(
                conn, sim_run_id=sim_run_id, financing_mode=financing_mode, state_date=as_of
            ),
            financing_mode=financing_mode,
        )
        if source_state is None:
            raise FinanceDataNotReady("historical_finance_position")

    expected_amount = sum((row.outstanding_amount_krw for row in eligible), start=Decimal(0))
    inventory = load_inventory_snapshot_as_of(
        conn,
        sim_run_id=sim_run_id,
        as_of=target_state_date,
    )
    base_state = target_state or source_state
    assert base_state is not None
    if base_state.unsettled_purchase_payables_krw < expected_amount:
        raise FinanceCancellationConflict("finance_unsettled_underflow")

    eligible_ids = tuple(row.purchase_id for row in eligible)
    changed = cancelled_payables(
        cancel_payables(conn, purchase_ids=list(eligible_ids), cancelled_date=as_of)
    )
    newly_cancelled_amount = checked_cancelled_amount(
        changed, eligible_ids=eligible_ids, expected_amount=expected_amount
    )

    if target_state is not None:
        finance_state_id = returned_state_id(
            subtract_existing_state(
                conn,
                finance_state_id=target_state.finance_state_id,
                cancelled_amount=newly_cancelled_amount,
                inventory_book_value_krw=inventory.inventory_book_value_krw,
                operational_inventory_value_krw=inventory.operational_inventory_value_krw,
            )
        )
    else:
        assert source_state is not None
        if source_state.financing_mode != financing_mode:
            raise FinanceCancellationConflict("finance_runtime_axis_mismatch")
        finance_state_id = returned_state_id(
            carry_and_subtract_state(
                conn,
                finance_state_id=daily_finance_state_id(
                    sim_run_id=sim_run_id,
                    financing_mode=financing_mode,
                    state_date=target_state_date,
                ),
                source_finance_state_id=source_state.finance_state_id,
                sim_run_id=sim_run_id,
                financing_mode=financing_mode,
                as_of=as_of,
                target_state_date=target_state_date,
                cancelled_amount=newly_cancelled_amount,
                inventory_book_value_krw=inventory.inventory_book_value_krw,
                operational_inventory_value_krw=inventory.operational_inventory_value_krw,
            )
        )

    return FinanceCancellationResult(
        requested_count=len(requested),
        newly_cancelled_count=len(changed),
        newly_cancelled_amount_krw=newly_cancelled_amount,
        finance_state_updated=True,
        finance_state_id=finance_state_id,
    )
