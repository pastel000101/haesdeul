"""Finance operations-console expense read model; strictly run-scoped.

★ 2026-09-29 재구성 BL-014: 응답 모델은 `schemas/console_expenses.py`, SQL 은
  `repository/console_expenses.py`.
"""

from datetime import date
from decimal import Decimal

from app.core import db as core_db
from app.finance.repository.console_expenses import load_console_expenses
from app.finance.schemas.console_expenses import (
    ConsoleExpenseCategoryTotal,
    ConsoleExpenseRow,
    ConsoleExpensesResponse,
    ConsoleExpenseSummary,
)

#: Screen wording for the categories the ledger actually stores.  The stored value
#: is the record; this only names it in Korean.
#:
#: A category missing from this table is **not** an error and is never re-bucketed
#: into "기타" — the row keeps its stored name in both fields, so a category added
#: to the ledger shows up as itself instead of quietly joining someone else's total.
_DISPLAY_NAMES: dict[str, str] = {
    "LABOR": "인건비",
    "PAYROLL": "급여",
    "LOGISTICS_SERVICE": "물류 용역비",
    "RENT": "임차료",
    "UTILITY": "수도광열비",
    "LOGISTICS": "물류비",
    "TRANSPORT": "운송비",
    "COMMISSION": "수수료",
    "INTEREST": "이자비용",
    "LOAN_INTEREST": "이자비용",
    "PACKAGING": "포장비",
    "DISPOSAL": "폐기비용",
    "OTHER": "기타",
}


def display_category(raw_category: str) -> str:
    """Name a stored category for the screen without reclassifying it."""
    return _DISPLAY_NAMES.get(raw_category, raw_category)


def get_console_expenses(
    *,
    sim_run_id: str,
    as_of: date,
    from_date: date | None = None,
    to_date: date | None = None,
    category: str | None = None,
    status: str | None = None,
) -> ConsoleExpensesResponse:
    """Read expenses for exactly one simulation run.

    Totals are built from the rows that were returned, so the number on screen is
    the sum of the lines under it.  A filtered view therefore totals the filtered
    lines — the alternative is a header that no visible row explains.

    🔴 **상태별 합계를 따로 센다.** 예전에는 «누적 비용» 한 칸뿐이라 아직 안 나간 돈과
       이미 나간 돈이 같은 숫자에 들어 있었다. 취소한 비용까지 그 안에 있었다 — 나가지
       않기로 한 돈이 비용 총액에 섞여 있으면 그 숫자로는 아무 판단도 못 한다.
    """
    rows: list[ConsoleExpenseRow] = []
    totals: dict[str, ConsoleExpenseCategoryTotal] = {}
    summary = ConsoleExpenseSummary(category_totals=[])
    with core_db.read_connection() as conn:
        expense_rows = load_console_expenses(
            conn,
            sim_run_id=sim_run_id,
            as_of=as_of,
            from_date=from_date,
            to_date=to_date,
            category=category,
            status=status,
        )
    for raw in expense_rows:
        stored = raw["amount_krw"]
        if stored is None:
            raise ValueError("expenses.amount_krw must not be null")
        amount = Decimal(str(stored))
        raw_category = str(raw["expense_category"])
        label = display_category(raw_category)
        row_status = "PAID" if raw.get("status") is None else str(raw["status"])
        paid_date = raw.get("paid_date")
        rows.append(
            ConsoleExpenseRow(
                expense_id=str(raw["expense_id"]),
                expense_date=raw["expense_date"],
                raw_category=raw_category,
                display_category=label,
                amount_krw=amount,
                source_ref=None if raw["evidence_id"] is None else str(raw["evidence_id"]),
                note=None if raw["note"] is None else str(raw["note"]),
                status=row_status,
                due_date=raw.get("due_date"),  # type: ignore[arg-type]
                paid_date=paid_date,  # type: ignore[arg-type]
                #  ★ 지급했다고 적혀 있는데 날짜가 없으면 «모른다» 다. 발생일을 대신
                #    넣지 않는다 — 화면은 사실만 말한다.
                paid_date_known=row_status == "PAID" and paid_date is not None,
                related_delivery_id=(
                    None
                    if raw.get("related_delivery_id") is None
                    else str(raw["related_delivery_id"])
                ),
            )
        )
        summary.total_expenses_krw += amount
        if row_status == "ACCRUED":
            summary.accrued_krw += amount
            summary.accrued_count += 1
        elif row_status == "PAID":
            summary.paid_krw += amount
        elif row_status == "CANCELLED":
            summary.cancelled_krw += amount
        bucket = totals.get(raw_category)
        if bucket is None:
            totals[raw_category] = ConsoleExpenseCategoryTotal(
                raw_category=raw_category,
                display_category=label,
                expense_count=1,
                total_amount_krw=amount,
            )
        else:
            bucket.expense_count += 1
            bucket.total_amount_krw += amount
    summary.category_totals = [totals[name] for name in sorted(totals)]
    return ConsoleExpensesResponse(
        sim_run_id=sim_run_id, as_of=as_of, summary=summary, rows=rows
    )
