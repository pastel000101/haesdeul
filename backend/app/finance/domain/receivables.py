"""판매 확정분 → 재무 매출채권 판정 — 판매 행 대조 · 쓰기 계획 · 같은 사실인지.

순서는 `service/receivables.py`, SQL 은 `repository/receivables.py`.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from app.finance.domain.values import decimal_value, row_value
from app.finance.schemas.data_port import FinanceDataNotReady
from app.finance.schemas.receivables import ReceivablePersistenceConflict, ReceivableWritePlan
from app.finance.schemas.sales_validation import ReceivableCreateInput


def one_sale_row(rows: list, *, sale_id: str) -> dict[str, Any]:
    if len(rows) != 1:
        raise ReceivablePersistenceConflict(f"sale was not found: {sale_id}")
    return dict(rows[0])


def build_receivable_write_plan(
    request: ReceivableCreateInput, *, sale_row: Mapping[str, Any], finance_state_id: str
) -> ReceivableWritePlan:
    """Sales header 1건을 Finance receivable 1건으로 옮기는 계획을 만든다."""

    sale_id = str(sale_row["sale_id"])
    if sale_id != request.sale_id:
        raise ReceivablePersistenceConflict("sale row does not match the requested sale_id")
    if str(sale_row["sim_run_id"]) != request.sim_run_id:
        raise ReceivablePersistenceConflict("sale row sim_run_id does not match the request")
    if row_value(sale_row, "customer_partner_id") != request.customer_partner_id:
        raise ReceivablePersistenceConflict(
            "sale row customer_partner_id does not match the request"
        )
    if row_value(sale_row, "sale_date") != request.sale_date:
        raise ReceivablePersistenceConflict("sale row sale_date does not match the request")
    if row_value(sale_row, "collection_due_date") != request.due_date:
        raise ReceivablePersistenceConflict("sale row due_date does not match the request")
    if decimal_value(row_value(sale_row, "total_amount_krw")) != request.original_amount_krw:
        raise ReceivablePersistenceConflict("sale row original amount does not match the request")

    due_date = request.due_date
    issued_date = request.issued_date
    if due_date is None or issued_date is None:
        raise ReceivablePersistenceConflict("sale row is missing receivable dates")
    return ReceivableWritePlan(
        receivable_id=receivable_id_for(sale_id),
        sale_id=sale_id,
        sim_run_id=request.sim_run_id,
        financing_mode=request.financing_mode,
        finance_state_id=finance_state_id,
        issued_date=issued_date,
        due_date=due_date,
        original_amount_krw=request.original_amount_krw,
        received_amount_krw=Decimal(0),
        outstanding_amount_krw=request.original_amount_krw,
        status="OPEN",
    )


def receivable_state_id(rows: list) -> str:
    """발행일의 재무 상태는 정확히 한 행이어야 한다."""
    if not rows:
        raise FinanceDataNotReady("finance_state_for_receivable")
    if len(rows) != 1:
        raise FinanceDataNotReady("finance_state_ambiguous")
    return str(row_value(rows[0], "finance_state_id", 0))


def receivable_id_for(sale_id: str) -> str:
    return f"AR-{sale_id}"


def assert_same_receivable(rows: list, plan: ReceivableWritePlan) -> None:
    """이미 있던 채권 행이 이번 계획과 같은 사실인지. 다르면 막는다."""
    if len(rows) != 1:
        raise ReceivablePersistenceConflict(
            f"receivable was not found after insert conflict: {plan.sale_id}"
        )
    row = rows[0]
    expected = {
        "receivable_id": plan.receivable_id,
        "sim_run_id": plan.sim_run_id,
        "sale_id": plan.sale_id,
        "issued_date": plan.issued_date,
        "due_date": plan.due_date,
        "original_amount_krw": plan.original_amount_krw,
        "received_amount_krw": plan.received_amount_krw,
        "outstanding_amount_krw": plan.outstanding_amount_krw,
        "status": plan.status,
    }
    for key, value in expected.items():
        if row_value(row, key) != value:
            raise ReceivablePersistenceConflict(
                f"conflicting receivable row for {plan.sale_id}: {key}"
            )
