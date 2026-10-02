"""판매 확정분 → 재무 매출채권 — 마스터 채권 발행 단계가 넘긴 연결로. commit 하지 않는다.

판정은 `domain/receivables.py`, SQL 은 `repository/receivables.py`.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.finance.domain.receivables import (
    assert_same_receivable,
    build_receivable_write_plan,
    one_sale_row,
    receivable_state_id,
)
from app.finance.repository.receivables import (
    add_state_receivables,
    insert_receivable,
    lock_receivable_state,
    lock_state_for_receivable,
    select_receivables_by_sale,
    select_sale_rows,
)
from app.finance.schemas.receivables import (
    ReceivablePersistenceConflict,
    ReceivableWritePlan,
    ReceivableWriteResult,
)
from app.finance.schemas.sales_validation import ReceivableCreateInput


def confirm_receivable(conn: Any, request: ReceivableCreateInput) -> ReceivableWriteResult:
    """확정된 Sale 을 읽어 receivable 과 Finance State 를 멱등 저장한다."""

    sale_row = load_sale_row(conn, request.sale_id)
    finance_state_id = load_finance_state_id_for_date(
        conn,
        sim_run_id=request.sim_run_id,
        financing_mode=request.financing_mode,
        state_date=request.issued_date,
    )
    plan = build_receivable_write_plan(
        request,
        sale_row=sale_row,
        finance_state_id=finance_state_id,
    )
    return persist_receivable(conn, plan)


def load_sale_row(conn: Any, sale_id: str) -> dict[str, Any]:
    """확정된 판매 헤더 한 행. 정확히 한 행이 아니면 막는다."""
    return one_sale_row(select_sale_rows(conn, sale_id=sale_id), sale_id=sale_id)


def load_finance_state_id_for_date(
    conn: Any, *, sim_run_id: str, financing_mode: str, state_date: date
) -> str:
    """지정한 Finance state를 ID 조립이 아니라 실행 축으로 찾는다.

    ``daily_finance_state_id``는 새 일별 상태를 만들 때 쓰는 결정론 ID 규칙이다.
    이미 존재하는 상태 조회의 정본 키는 ``(sim_run_id, financing_mode, state_date)``이며,
    조회된 실제 ``finance_state_id``를 receivable lineage에 연결한다.

    Receivable은 실제 원장 발행일의 State에만 반영한다. 따라서 휴장일 판매가
    다음 개장일에 발행되더라도 과거 ``sale_date`` State를 소급 수정하지 않는다.
    """
    return receivable_state_id(
        lock_receivable_state(
            conn, sim_run_id=sim_run_id, financing_mode=financing_mode, state_date=state_date
        )
    )


def persist_receivable(conn: Any, plan: ReceivableWritePlan) -> ReceivableWriteResult:
    """caller-owned connection으로 receivable과 Finance State AR을 멱등 저장한다."""
    finance_rows = lock_state_for_receivable(conn, finance_state_id=plan.finance_state_id)
    if len(finance_rows) != 1:
        raise ReceivablePersistenceConflict(
            f"finance state was not found: {plan.finance_state_id}"
        )

    insert_count = insert_receivable(conn, plan)
    update_count = 0
    if insert_count:
        update_count = add_state_receivables(
            conn, finance_state_id=plan.finance_state_id, delta=plan.original_amount_krw
        )
        if update_count != 1:
            raise ReceivablePersistenceConflict(
                "finance state receivables could not be updated"
            )
    else:
        assert_same_receivable(select_receivables_by_sale(conn, sale_id=plan.sale_id), plan)
    return ReceivableWriteResult(
        receivable_id=plan.receivable_id,
        finance_state_id=plan.finance_state_id,
        receivables_written=insert_count,
        finance_state_updates=update_count,
    )
