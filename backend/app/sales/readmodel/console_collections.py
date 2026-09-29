"""Sales operations-console collection read model; strictly run-scoped.

🔴 **There is no collection table and this module does not invent one.**  What a
collection *is* — money owed against a confirmed sale — is already recorded in
`receivables`.  A second entity here would be a second truth about the same money.

🔴 **Aging comes from Finance.**  `app.contracts.aging.classify_receivable_aging` is
the one rule; a Sales-local copy would let the 수금 screen and the 채권 screen put
the same receivable in different buckets, and neither would be wrong on its own.

★ 2026-09-29 BL-013: `sales/console_collections.py` 에서 옮겼다. SQL 은
  `repository/console_collections.py`, 채권 상태 규칙은 `domain/receivable_history.py` 다.
"""

from datetime import date
from decimal import Decimal

from app.contracts.aging import AgingBucket, classify_receivable_aging
from app.core import db as core_db
from app.sales.domain.receivable_history import projected_status
from app.sales.repository.console_collections import load_collection_rows
from app.sales.schemas.console_collections import (
    ConsoleCollectionRow,
    ConsoleCollectionsResponse,
    ConsoleCollectionSummary,
)

_ZERO = Decimal(0)


def get_console_collections(
    *,
    sim_run_id: str,
    as_of: date,
    partner_id: str | None = None,
    aging_bucket: AgingBucket | None = None,
    status: str | None = None,
) -> ConsoleCollectionsResponse:
    """Collections for exactly one simulation run.

    The summary counts every receivable in the run, not just the filtered rows —
    a filter narrows what is listed, never what is owed.
    """
    with core_db.read_connection() as conn:
        summary = ConsoleCollectionSummary()
        rows: list[ConsoleCollectionRow] = []
        for raw in load_collection_rows(conn, sim_run_id=sim_run_id, as_of=as_of):
            outstanding = raw["outstanding_amount_krw"]
            if outstanding is None:
                raise ValueError("receivables.outstanding_amount_krw must not be null")
            amount = Decimal(str(outstanding))
            received = _ZERO if raw["received_amount_krw"] is None else Decimal(
                str(raw["received_amount_krw"])
            )
            bucket, overdue = classify_receivable_aging(
                outstanding_amount_krw=amount, due_date=raw["due_date"], as_of=as_of
            )
            summary.collected_krw += received
            if bucket != "PAID":
                summary.total_outstanding_krw += amount
                if overdue:
                    summary.overdue_krw += amount
            row_partner = None if raw["partner_id"] is None else str(raw["partner_id"])
            #  🔴 저장된 status 는 덮여 쓰인다. 복원한 금액에서 다시 세운다.
            row_status = projected_status(
                original_amount_krw=Decimal(str(raw["original_amount_krw"])),
                received_amount_krw=received,
            )
            if partner_id is not None and row_partner != partner_id:
                continue
            if aging_bucket is not None and bucket != aging_bucket:
                continue
            if status is not None and row_status != status:
                continue
            rows.append(
                ConsoleCollectionRow(
                    partner_id=row_partner,
                    partner_name=None if raw["partner_name"] is None else str(raw["partner_name"]),
                    sale_id=str(raw["sale_id"]),
                    receivable_id=str(raw["receivable_id"]),
                    original_amount_krw=Decimal(str(raw["original_amount_krw"])),
                    received_amount_krw=received,
                    outstanding_amount_krw=amount,
                    due_date=raw["due_date"],
                    days_overdue=overdue,
                    aging_bucket=bucket,
                    status=row_status,
                )
            )
        return ConsoleCollectionsResponse(
            sim_run_id=sim_run_id, as_of=as_of, summary=summary, rows=rows
        )
