"""Finance operations-console receivable read model; strictly run-scoped.

기준일 시점의 상태를 복원해서 읽는다(`repository/receivable_history.py`). `receivables` 행의
수금 칸은 덮여 쓰이므로 그대로 읽으면 과거 화면에 미래 수금이 실린다 — 그 모듈의 머리말에 V13
실측이 적혀 있다.

응답 모델은 `schemas/console_receivables.py`, SQL 은 `repository/console_receivables.py`, 상태
규칙은 계약 `app/contracts/receivable_history.py`.
"""

from datetime import date
from decimal import Decimal

from app.contracts.aging import AgingBucket, classify_receivable_aging
from app.contracts.receivable_history import projected_status
from app.core import db as core_db
from app.finance.repository.console_receivables import select_console_receivables
from app.finance.schemas.console_receivables import (
    ConsoleReceivableRow,
    ConsoleReceivablesResponse,
    ConsoleReceivableSummary,
)


def get_console_receivables(
    *,
    sim_run_id: str,
    as_of: date,
    aging_bucket: AgingBucket | None = None,
    partner_id: str | None = None,
) -> ConsoleReceivablesResponse:
    """Read only receivables belonging to the caller's explicit simulation run."""
    with core_db.read_connection() as conn:
        receivable_rows = select_console_receivables(conn, sim_run_id=sim_run_id, as_of=as_of)
    rows: list[ConsoleReceivableRow] = []
    summary = ConsoleReceivableSummary()
    for raw in receivable_rows:
        outstanding = raw["outstanding_amount_krw"]
        if outstanding is None:
            raise ValueError("receivables.outstanding_amount_krw must not be null")
        amount = Decimal(str(outstanding))
        original = Decimal(str(raw["original_amount_krw"]))
        received = Decimal(str(raw["received_amount_krw"]))
        bucket, overdue = classify_receivable_aging(
            outstanding_amount_krw=amount, due_date=raw["due_date"], as_of=as_of
        )
        if (aging_bucket is None or bucket == aging_bucket) and (
            partner_id is None or raw["partner_id"] == partner_id
        ):
            rows.append(
                ConsoleReceivableRow(
                    receivable_id=str(raw["receivable_id"]),
                    sale_id=str(raw["sale_id"]),
                    partner_id=None if raw["partner_id"] is None else str(raw["partner_id"]),
                    partner_name=None if raw["partner_name"] is None else str(raw["partner_name"]),
                    original_amount_krw=original,
                    received_amount_krw=received,
                    outstanding_amount_krw=amount,
                    due_date=raw["due_date"],
                    days_overdue=overdue,
                    aging_bucket=bucket,
                    # 저장된 status 를 읽지 않는다. 그 칸도 덮여 쓰인다 —
                    # 복원한 금액과 갈리면 «미수 282,426 인데 COLLECTED» 가 된다.
                    status=projected_status(
                        original_amount_krw=original, received_amount_krw=received
                    ),
                )
            )
        if bucket != "PAID":
            summary.total_outstanding_krw += amount
            if bucket == "CURRENT":
                summary.current_krw += amount
            elif bucket == "1_7":
                summary.days_1_7_krw += amount
            elif bucket == "8_30":
                summary.days_8_30_krw += amount
            elif bucket == "30_PLUS":
                summary.days_30_plus_krw += amount
    return ConsoleReceivablesResponse(
        sim_run_id=sim_run_id, as_of=as_of, summary=summary, rows=rows
    )
