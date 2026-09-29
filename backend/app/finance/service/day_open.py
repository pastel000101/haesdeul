"""재무 개장 — 마스터 개장 경계가 넘긴 연결로 그날 상태를 물려받아 세운다. **commit 하지 않는다.**

★ 2026-09-29 재구성 BL-014: `finance/day_open.py` 를 판정 · 순서 · SQL 로 나눴다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.finance.domain.day_open import day_open_axis, has_exact_state
from app.finance.domain.state_identity import daily_finance_state_id
from app.finance.repository.day_open import (
    carry_forward_state,
    select_day_open_axis,
    select_exact_states,
)
from app.finance.schemas.data_port import FinanceDataNotReady
from app.finance.schemas.finance_state import DAY_OPEN_STATE_TYPE
from app.finance.service.inventory import load_inventory_snapshot_as_of


def is_day_open(conn: Any, *, sim_run_id: str | None, as_of: date) -> bool:
    """이 실행의 재무 축에 ``as_of`` 당일 상태가 정확히 있는지. 받은 연결로 읽는다."""
    run_id, financing_mode = day_open_axis(select_day_open_axis(conn, sim_run_id=sim_run_id))
    return has_exact_state(
        select_exact_states(
            conn, sim_run_id=run_id, financing_mode=financing_mode, state_date=as_of
        )
    )


def open_finance_day(
    conn: Any, *, sim_run_id: str | None, as_of: date, carry_from: date
) -> None:
    """``carry_from`` 의 정확한 상태를 ``as_of`` 로 멱등하게 물려받아 세운다.

    Finance 고유 값은 이어 가되 재고가치는 ``as_of`` 시점의 Inventory Ledger에서
    다시 계산한다. ``financial_limit_krw``는 PostgreSQL 생성 컬럼이므로 insert에서
    제외한다. 연결은 마스터 것이고 commit 하지 않는다.
    """
    run_id, financing_mode = day_open_axis(select_day_open_axis(conn, sim_run_id=sim_run_id))
    if has_exact_state(
        select_exact_states(
            conn, sim_run_id=run_id, financing_mode=financing_mode, state_date=as_of
        )
    ):
        return
    if not has_exact_state(
        select_exact_states(
            conn, sim_run_id=run_id, financing_mode=financing_mode, state_date=carry_from
        )
    ):
        raise FinanceDataNotReady("historical_finance_position")

    inventory = load_inventory_snapshot_as_of(
        conn,
        sim_run_id=run_id,
        as_of=as_of,
    )
    inserted = carry_forward_state(
        conn,
        {
            "finance_state_id": daily_finance_state_id(
                sim_run_id=run_id,
                financing_mode=financing_mode,
                state_date=as_of,
            ),
            "sim_run_id": run_id,
            "financing_mode": financing_mode,
            "as_of": as_of,
            "carry_from": carry_from,
            "state_type": DAY_OPEN_STATE_TYPE,
            "inventory_book_value_krw": inventory.inventory_book_value_krw,
            "operational_inventory_value_krw": (
                inventory.operational_inventory_value_krw
            ),
            "note": f"하루 넘김이 {carry_from} 재무 상태에서 물려받아 세운 행",
        },
    )
    if not inserted and not has_exact_state(
        select_exact_states(
            conn, sim_run_id=run_id, financing_mode=financing_mode, state_date=as_of
        )
    ):
        raise FinanceDataNotReady("historical_finance_position")
