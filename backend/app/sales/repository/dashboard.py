"""판매 현황 SQL — 실행 하나 · 기준일 하나의 판매 · 채권 사실.

연결을 인자로 받는다. 응답 조립은 `readmodel/dashboard.py` 다.
"""

from datetime import date

from psycopg import Connection, sql

from app.core.settings import get_db_schema
from app.sales.repository._cursor import fetch_all, fetch_one
from app.sales.repository.receivable_history import history_columns, history_join


def load_sales_dashboard_meta(
    conn: Connection, *, sim_run_id: str, as_of: date
) -> dict[str, object] | None:
    query = sql.SQL(
        """
        SELECT sim_run_id, %s::date AS as_of, run_type AS data_type
        FROM {}.sim_runs
        WHERE sim_run_id = %s
        """
    ).format(sql.Identifier(get_db_schema()))
    return fetch_one(conn, query, [as_of, sim_run_id])


def load_sales_summary(
    conn: Connection, *, sim_run_id: str, as_of: date
) -> dict[str, object] | None:
    """이 실행의 판매 합계. 미수는 기준일 시점으로 복원한다.

    `receivables` 의 수금 칸을 그대로 더하면 과거 기준일 KPI 에 미래 수금이 실린다
    (`repository/receivable_history.py`).
    """
    schema = get_db_schema()
    query = (
        sql.SQL(
            """
        SELECT
            COUNT(*)::int AS sales_count,
            COUNT(DISTINCT s.customer_partner_id)::int AS customer_count,
            COALESCE(SUM(s.total_quantity_kg), 0) AS total_sales_quantity_kg,
            COALESCE(SUM(s.total_amount_krw), 0) AS total_sales_amount_krw,
            COALESCE(SUM(s.contribution_profit_krw), 0) AS contribution_profit_krw,
            COALESCE(SUM(COALESCE(collected.target_received_total_krw, 0)), 0)
                AS received_amount_krw,
            COALESCE(
                SUM(r.original_amount_krw - COALESCE(collected.target_received_total_krw, 0)),
                0
            ) AS outstanding_receivables_krw
        FROM {}.sales AS s
        LEFT JOIN {}.receivables AS r
          ON r.sale_id = s.sale_id
         AND r.sim_run_id = s.sim_run_id
         AND r.issued_date <= %s
        """
        )
        .format(sql.Identifier(schema), sql.Identifier(schema))
        + history_join(schema)
        + sql.SQL(
            """
        WHERE s.sim_run_id = %s
          AND s.sale_date <= %s
        """
        )
    )
    # 주의: 발행일 · 수금 복원 기준일 · 실행 축 · 판매일 순이다.
    return fetch_one(conn, query, [as_of, as_of, sim_run_id, as_of])


def load_collection_summary(
    conn: Connection, *, sim_run_id: str, as_of: date
) -> list[dict[str, object]]:
    query = sql.SQL(
        """
        SELECT
            s.collection_status,
            COUNT(*)::int AS count,
            COALESCE(SUM(s.total_amount_krw), 0) AS sales_amount_krw
        FROM {}.sales AS s
        WHERE s.sim_run_id = %s
          AND s.sale_date <= %s
        GROUP BY s.collection_status
        ORDER BY s.collection_status
        """
    ).format(sql.Identifier(get_db_schema()))
    return fetch_all(conn, query, [sim_run_id, as_of])


def load_item_summaries(
    conn: Connection, *, sim_run_id: str, as_of: date
) -> list[dict[str, object]]:
    schema = get_db_schema()
    query = sql.SQL(
        """
        SELECT
            si.item_id,
            i.item_name,
            COUNT(*)::int AS line_count,
            COALESCE(SUM(si.quantity_kg), 0) AS total_quantity_kg,
            COALESCE(SUM(si.line_amount_krw), 0) AS sales_amount_krw,
            COALESCE(SUM(si.contribution_profit_krw), 0) AS contribution_profit_krw,
            COALESCE(SUM(si.line_amount_krw) / NULLIF(SUM(si.quantity_kg), 0), 0)
                AS avg_unit_price_krw_per_kg
        FROM {}.sale_items AS si
        JOIN {}.sales AS s
          ON s.sale_id = si.sale_id
        JOIN {}.items AS i
          ON i.item_id = si.item_id
        WHERE s.sim_run_id = %s
          AND s.sale_date <= %s
        GROUP BY si.item_id, i.item_name
        ORDER BY sales_amount_krw DESC, si.item_id
        """
    ).format(sql.Identifier(schema), sql.Identifier(schema), sql.Identifier(schema))
    return fetch_all(conn, query, [sim_run_id, as_of])


def load_recent_sales(
    conn: Connection, *, sim_run_id: str, as_of: date, limit: int
) -> list[dict[str, object]]:
    schema = get_db_schema()
    query = sql.SQL(
        """
        SELECT
            s.sale_id,
            s.order_date,
            s.sale_date,
            s.customer_partner_id,
            p.partner_name,
            s.total_quantity_kg,
            s.total_amount_krw,
            s.contribution_profit_krw,
            s.collection_due_date,
            s.collection_status,
            s.order_status
        FROM {}.sales AS s
        LEFT JOIN {}.partners AS p
          ON p.partner_id = s.customer_partner_id
        WHERE s.sim_run_id = %s
          AND s.sale_date <= %s
        ORDER BY s.sale_date DESC, s.sale_id DESC
        LIMIT %s
        """
    ).format(sql.Identifier(schema), sql.Identifier(schema))
    return fetch_all(conn, query, [sim_run_id, as_of, limit])


def load_today_confirmed_sales(
    conn: Connection, *, sim_run_id: str, as_of: date
) -> list[dict[str, object]]:
    """기준일에 확정된 판매 원장 품목 행을 읽는다.

    판매 확정일(``order_date``)과 납품일(``sale_date``)은 다른 사실이다. 이 목록은
    오늘 판매하기로 확정한 주문을 보여 주므로 미래 납품 건도 포함한다. 납품/실적 기준
    dashboard 집계는 ``load_recent_sales`` 등 기존 read model이 계속 소유한다.
    """
    schema = get_db_schema()
    query = sql.SQL(
        """
        SELECT
            s.sale_id,
            s.order_date,
            s.sale_date,
            s.customer_partner_id,
            p.partner_name,
            si.item_id,
            i.item_name,
            si.quantity_kg,
            si.unit_price_krw_per_kg,
            si.line_amount_krw,
            s.order_status
        FROM {}.sales AS s
        JOIN {}.sale_items AS si
          ON si.sale_id = s.sale_id
        LEFT JOIN {}.items AS i
          ON i.item_id = si.item_id
        LEFT JOIN {}.partners AS p
          ON p.partner_id = s.customer_partner_id
        WHERE s.sim_run_id = %s
          AND s.order_date = %s
          AND s.order_status IN ('CONFIRMED', 'DELIVERED')
        ORDER BY s.sale_id DESC, si.item_id
        """
    ).format(
        sql.Identifier(schema),
        sql.Identifier(schema),
        sql.Identifier(schema),
        sql.Identifier(schema),
    )
    return fetch_all(conn, query, [sim_run_id, as_of])


def load_sales_receivables(
    conn: Connection, *, sim_run_id: str, as_of: date
) -> list[dict[str, object]]:
    schema = get_db_schema()
    query = (
        sql.SQL(
            """
        SELECT
            r.receivable_id,
            r.sale_id,
            s.sale_date,
            s.customer_partner_id,
            p.partner_name,
            r.issued_date,
            r.due_date,
            r.original_amount_krw,
        """
        )
        + history_columns()
        + sql.SQL(
            """
        FROM {}.receivables AS r
        JOIN {}.sales AS s
          ON s.sale_id = r.sale_id
         AND s.sim_run_id = r.sim_run_id
        LEFT JOIN {}.partners AS p
          ON p.partner_id = s.customer_partner_id
        """
        ).format(sql.Identifier(schema), sql.Identifier(schema), sql.Identifier(schema))
        + history_join(schema)
        + sql.SQL(
            """
        WHERE r.sim_run_id = %s
          AND r.issued_date <= %s
          AND s.sale_date <= %s
        ORDER BY r.due_date ASC, r.receivable_id ASC
        """
        )
    )
    # 주의: `%s` 는 네 개다 — LATERAL 의 기준일이 WHERE 보다 먼저 온다.
    return fetch_all(conn, query, [as_of, sim_run_id, as_of, as_of])
