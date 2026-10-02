"""판매 추이 SQL — 날짜별로 접은 판매 사실.

기간 검증과 상한 계산은 `readmodel/console_trend.py` 다.
"""

from datetime import date
from typing import Any

from psycopg import Connection, sql

from app.core.settings import get_db_schema
from app.sales.repository._cursor import fetch_all


def load_daily_sales(
    conn: Connection, *, sim_run_id: str, upper: date, from_date: date | None, limit: int
) -> list[dict[str, Any]]:
    """이 실행의 날짜별 판매. 최근 `limit` 일을 골라 오래된 날부터 돌려준다."""
    schema = get_db_schema()
    query = sql.SQL(
        """
        SELECT *
        FROM (
            SELECT
                s.sale_date,
                COUNT(*)::int AS sales_count,
                COALESCE(SUM(s.total_quantity_kg), 0) AS quantity_kg,
                COALESCE(SUM(s.total_amount_krw), 0) AS sales_amount_krw,
                COALESCE(SUM(s.contribution_profit_krw), 0) AS contribution_profit_krw
            FROM {}.sales AS s
            WHERE s.sim_run_id = %s
              AND s.sale_date <= %s
              AND (%s::date IS NULL OR s.sale_date >= %s)
              AND s.sale_date <= %s
            GROUP BY s.sale_date
            ORDER BY s.sale_date DESC
            LIMIT %s
        ) AS recent
        ORDER BY sale_date ASC
        """
    ).format(sql.Identifier(schema))
    return fetch_all(conn, query, [sim_run_id, upper, from_date, from_date, upper, limit])
