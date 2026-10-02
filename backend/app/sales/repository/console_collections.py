"""판매 운영 콘솔의 수금 SQL — 실행 하나의 채권과 그 판매의 거래처."""

from datetime import date

from psycopg import Connection, sql

from app.core.settings import get_db_schema
from app.sales.repository._cursor import fetch_all
from app.sales.repository.receivable_history import history_columns, history_join


def load_collection_rows(
    conn: Connection, *, sim_run_id: str, as_of: date
) -> list[dict[str, object]]:
    """Receivables of one run, with the partner the sale was made to."""
    schema = get_db_schema()
    statement = (
        sql.SQL(
            """
        SELECT r.receivable_id, r.sale_id, r.due_date, r.original_amount_krw,
               s.customer_partner_id AS partner_id, p.partner_name,
        """
        )
        + history_columns()
        + sql.SQL(
            """
        FROM {schema}.receivables r
        JOIN {schema}.sales s ON s.sale_id = r.sale_id AND s.sim_run_id = r.sim_run_id
        LEFT JOIN {schema}.partners p ON p.partner_id = s.customer_partner_id
        """
        ).format(schema=sql.Identifier(schema))
        + history_join(schema)
        + sql.SQL(
            """
        WHERE r.sim_run_id = %s AND r.issued_date <= %s
        ORDER BY r.due_date ASC, r.receivable_id ASC
        """
        )
    )
    # 주의: `%s` 는 세 개다 — LATERAL 의 기준일이 WHERE 보다 먼저 온다.
    return fetch_all(conn, statement, [as_of, sim_run_id, as_of])
