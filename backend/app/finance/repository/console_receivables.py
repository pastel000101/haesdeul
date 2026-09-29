"""운영 콘솔 매출채권 SQL — 수금 칸은 기준일 시점으로 복원한다.

★ 2026-09-29 재구성 BL-014: `finance/console_receivables.py` 에서 옮겼다(문면 그대로).
"""

from datetime import date
from typing import Any

from psycopg import sql

from app.core.settings import get_db_schema
from app.finance.repository._cursor import fetch_all
from app.finance.repository.receivable_history import history_columns, history_join


def select_console_receivables(conn: Any, *, sim_run_id: str, as_of: date) -> list:
    """이 실행에서 기준일까지 발행된 채권 — 수금 칸은 기준일 시점으로 복원한다."""
    schema = get_db_schema()
    query = (
        sql.SQL(
            """
        SELECT r.receivable_id, r.sale_id, s.customer_partner_id AS partner_id,
               p.partner_name, r.original_amount_krw, r.due_date,
        """
        )
        + history_columns()
        + sql.SQL(
            """
        FROM {}.receivables r
        LEFT JOIN {}.sales s ON s.sale_id = r.sale_id AND s.sim_run_id = r.sim_run_id
        LEFT JOIN {}.partners p ON p.partner_id = s.customer_partner_id
        """
        ).format(sql.Identifier(schema), sql.Identifier(schema), sql.Identifier(schema))
        + history_join(schema)
        + sql.SQL(
            """
        WHERE r.sim_run_id = %s AND r.issued_date <= %s
        ORDER BY r.due_date ASC, r.receivable_id ASC
        """
        )
    )
    #  ⚠️ `%s` 는 세 개다 — LATERAL 의 기준일이 WHERE 보다 **먼저** 온다.
    return fetch_all(conn, query, [as_of, sim_run_id, as_of])
