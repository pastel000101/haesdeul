"""사용자 기록 자금 입·출금 — 화면 `POST /finance/cash-adjustments` 와 마스터 ask 가 같은 함수를
부른다.

★ `apply_cash_adjustment` 가 한 요청 = 한 트랜잭션의 경계를 연다(받은 연결에 `transaction`).
  `record_cash_adjustment` 는 그 안의 순서다 — commit 하지 않는다.

★ 2026-09-29 재구성 BL-014: `finance/cash_adjustments.py` 의 순서와 `finance/router.py` 핸들러 안의
  트랜잭션을 옮겼다.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.core import db as core_db
from app.finance.domain import messages
from app.finance.domain.cash_adjustments import cash_adjustment_delta, cash_after_adjustment
from app.finance.domain.finance_state import exact_state_row
from app.finance.repository.cash_adjustments import insert_cash_adjustment
from app.finance.repository.finance_states import lock_state_on_date, update_state_cash
from app.finance.schemas.cash_adjustments import (
    CashAdjustmentChange,
    CashAdjustmentConflict,
    CashAdjustmentResult,
    CashCategory,
    CashDirection,
)
from app.finance.schemas.data_port import FinanceDataNotReady
from app.finance.schemas.write_rejection import FinanceWriteRejected


def record_cash_adjustment(
    conn,
    *,
    sim_run_id: str,
    financing_mode: str,
    adjustment_date: date,
    direction: CashDirection,
    category: CashCategory,
    amount_krw: Decimal,
    source_ref: str,
    note: str | None,
    recorded_by: str,
) -> CashAdjustmentResult:
    """한 기준일 state에 실제 자금 입·출금을 원자적으로 반영한다.

    순서: 금액 확인 → 그날 재무 상태 한 행 잠금 → 조정 뒤 현금 확인 → 조정 원장 적재 →
    상태 현금 갱신. commit 은 부르는 쪽이 한다.
    """
    delta = cash_adjustment_delta(direction=direction, amount_krw=amount_krw)
    state = exact_state_row(
        lock_state_on_date(
            conn,
            sim_run_id=sim_run_id,
            financing_mode=financing_mode,
            state_date=adjustment_date,
        )
    )
    next_cash = cash_after_adjustment(state["current_cash_krw"], delta)
    adjustment_id = f"CASH-ADJ-{uuid4()}"
    insert_cash_adjustment(
        conn,
        cash_adjustment_id=adjustment_id,
        sim_run_id=sim_run_id,
        financing_mode=financing_mode,
        adjustment_date=adjustment_date,
        direction=direction,
        category=category,
        amount_krw=amount_krw,
        source_ref=source_ref,
        note=note,
        recorded_by=recorded_by,
    )
    updated = update_state_cash(
        conn, finance_state_id=state["finance_state_id"], current_cash=next_cash
    )
    if updated != 1:
        raise CashAdjustmentConflict("재무 상태를 갱신하지 못했습니다.")
    return CashAdjustmentResult(adjustment_id, next_cash)


def apply_cash_adjustment(
    conn: core_db.Connection, change: CashAdjustmentChange
) -> dict[str, object]:
    """사용자 자금 입금·출금을 근거와 함께 기록한다 — **한 요청 = 한 트랜잭션.**

    화면 `POST /finance/cash-adjustments` 와 마스터 ask(FINANCE_CASH_ADJUSTMENT_CREATE)가 같은
    함수를 부른다. 받지 않은 요청: 조정 충돌(금액 · 출금 뒤 현금 · 갱신 실패) CONFLICT, 그날
    재무 상태 없음 CONFLICT(문장은 «해당 기준일의 재무 상태…»).
    """
    try:
        with core_db.transaction(conn):
            result = record_cash_adjustment(conn, **change.model_dump())
        return {
            "cash_adjustment_id": result.cash_adjustment_id,
            "current_cash_krw": result.current_cash_krw,
        }
    except CashAdjustmentConflict as error:
        raise FinanceWriteRejected("CONFLICT", str(error)) from error
    except FinanceDataNotReady as error:
        raise FinanceWriteRejected("CONFLICT", messages.STATE_NOT_READY_ON_DATE) from error
