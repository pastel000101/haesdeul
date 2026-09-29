"""운영 콘솔 운영비 SQL.

★ 2026-09-29 재구성 BL-014: `finance/console_expenses.py` 에서 옮겼다(문면 그대로).
"""

from datetime import date
from typing import Any

from psycopg import sql

from app.core.settings import get_db_schema
from app.finance.repository._cursor import fetch_all


def load_console_expenses(
    conn: Any,
    *,
    sim_run_id: str,
    as_of: date,
    from_date: date | None = None,
    to_date: date | None = None,
    category: str | None = None,
    status: str | None = None,
) -> list[dict[str, object]]:
    """Expense rows for one run.  `as_of` is the ceiling the run has reached.

    ★ **발생일로 자른다.** `as_of` 는 그 실행이 도달한 날이고, 그날까지 «생긴» 비용을
      보여 준다 — 지급 예정일이 그 뒤인 `ACCRUED` 도 포함이다. 앞으로 나갈 돈을 화면에서
      빼면 이 칸을 만든 이유가 없어진다.
    """
    schema = get_db_schema()
    conditions: list[sql.Composable] = [
        sql.SQL("sim_run_id = %s"),
        sql.SQL("expense_date <= %s"),
    ]
    params: list[object] = [sim_run_id, as_of]
    if from_date is not None:
        conditions.append(sql.SQL("expense_date >= %s"))
        params.append(from_date)
    if to_date is not None:
        conditions.append(sql.SQL("expense_date <= %s"))
        params.append(to_date)
    if category is not None:
        conditions.append(sql.SQL("expense_category = %s"))
        params.append(category)
    if status is not None:
        conditions.append(sql.SQL("status = %s"))
        params.append(status)
    query = (
        sql.SQL(
            """
            SELECT expense_id, expense_date, expense_category, amount_krw,
                   evidence_id, note, status, due_date, paid_date,
                   related_delivery_id
            FROM {}.expenses
            WHERE
            """
        ).format(sql.Identifier(schema))
        + sql.SQL(" AND ").join(conditions)
        + sql.SQL(" ORDER BY expense_date DESC, expense_id ASC")
    )
    return fetch_all(conn, query, params)
