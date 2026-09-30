"""판매 운영 콘솔의 거래처 SQL — 거래처 원장 행과 한 실행의 판매 · 채권 집계.

★ 2026-09-29 BL-013: `sales/console_partners.py` 에서 SQL 을 옮겼다. 상세 조회가 쓰던 private
  함수 다섯(`_load_basic` 등)은 readmodel 이 부르므로 공개 이름(`load_partner_*`)으로 올렸다.
"""

from datetime import date

from psycopg import Connection, sql

from app.core.settings import get_db_schema
from app.sales.repository._cursor import fetch_all
from app.sales.repository.receivable_history import history_columns, history_join


def load_partner_rows(
    conn: Connection,
    *,
    sim_run_id: str,
    as_of: date,
    query: str | None = None,
    status: str | None = None,
    partner_type: str | None = None,
) -> list[dict[str, object]]:
    """Partner masters joined to the aggregates of one run.

    The aggregate sub-selects carry `sim_run_id` themselves; the partner row does
    not, because the master table has no run axis to filter on.
    """
    schema = get_db_schema()
    conditions: list[sql.Composable] = []
    # Placeholder order below, read top-to-bottom through the statement:
    #   sales sub-select      sim_run_id, as_of
    #   receivable sub-select as_of (the overdue FILTER), as_of (the collection
    #                         restore), sim_run_id, as_of (issued_date)
    #  ⚠️ 하나라도 어긋나면 다른 실행이나 다른 날짜가 조용히 섞인다.
    params: list[object] = [sim_run_id, as_of, as_of, as_of, sim_run_id, as_of]
    if query is not None:
        conditions.append(sql.SQL("(p.partner_id ILIKE %s OR p.partner_name ILIKE %s)"))
        params.extend([f"%{query}%", f"%{query}%"])
    if status is not None:
        conditions.append(sql.SQL("(CASE WHEN p.active THEN 'ACTIVE' ELSE 'INACTIVE' END) = %s"))
        params.append(status)
    if partner_type is not None:
        conditions.append(sql.SQL("p.partner_type = %s"))
        params.append(partner_type)
    statement = sql.SQL(
        """
        SELECT p.partner_id, p.partner_name, p.partner_type, p.active,
               COALESCE(s.total_sales_krw, 0) AS total_sales_krw,
               COALESCE(s.total_sales_count, 0) AS total_sales_count,
               s.latest_sale_date,
               COALESCE(r.receivable_balance_krw, 0) AS receivable_balance_krw,
               COALESCE(r.overdue_balance_krw, 0) AS overdue_balance_krw
        FROM {schema}.partners p
        LEFT JOIN (
            SELECT customer_partner_id,
                   SUM(total_amount_krw) AS total_sales_krw,
                   COUNT(*)::int AS total_sales_count,
                   MAX(sale_date) AS latest_sale_date
            FROM {schema}.sales
            WHERE sim_run_id = %s AND sale_date <= %s
            GROUP BY customer_partner_id
        ) s ON s.customer_partner_id = p.partner_id
        LEFT JOIN (
            SELECT sa.customer_partner_id,
                   SUM(rc.original_amount_krw - rc.received_as_of_krw)
                       AS receivable_balance_krw,
                   SUM(rc.original_amount_krw - rc.received_as_of_krw)
                       FILTER (WHERE rc.due_date < %s) AS overdue_balance_krw
            FROM (
                SELECT r.sale_id, r.sim_run_id, r.due_date, r.original_amount_krw,
                       COALESCE(collected.target_received_total_krw, 0) AS received_as_of_krw
                FROM {schema}.receivables r
                LEFT JOIN LATERAL (
                    SELECT event.target_received_total_krw
                    FROM {schema}.master_collection_events AS event
                    WHERE event.sim_run_id = r.sim_run_id
                      AND event.receivable_id = r.receivable_id
                      AND event.collection_date <= %s
                    ORDER BY event.collection_date DESC,
                             event.target_received_total_krw DESC
                    LIMIT 1
                ) AS collected ON TRUE
                WHERE r.sim_run_id = %s AND r.issued_date <= %s
            ) rc
            JOIN {schema}.sales sa
              ON sa.sale_id = rc.sale_id AND sa.sim_run_id = rc.sim_run_id
            GROUP BY sa.customer_partner_id
        ) r ON r.customer_partner_id = p.partner_id
        """
    ).format(schema=sql.Identifier(schema))
    if conditions:
        statement += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)
    statement += sql.SQL(" ORDER BY p.partner_id ASC")
    return fetch_all(conn, statement, params)


def load_partner_basic(conn: Connection, *, partner_id: str) -> dict[str, object] | None:
    schema = get_db_schema()
    statement = sql.SQL(
        """
        SELECT partner_id, partner_name, partner_type, client_type, factory_region,
               sales_collection_days, pricing_contract_type, active
        FROM {}.partners WHERE partner_id = %s
        """
    ).format(sql.Identifier(schema))
    rows = fetch_all(conn, statement, [partner_id])
    return None if not rows else rows[0]


def load_partner_sales(
    conn: Connection, *, sim_run_id: str, as_of: date, partner_id: str, limit: int
) -> list[dict]:
    schema = get_db_schema()
    statement = sql.SQL(
        """
        SELECT s.sale_id, s.sale_date, s.total_quantity_kg, s.total_amount_krw,
               s.contribution_profit_krw, i.item_id, it.item_name,
               i.unit_price_krw_per_kg
        FROM {schema}.sales s
        LEFT JOIN LATERAL (
            SELECT item_id, unit_price_krw_per_kg
            FROM {schema}.sale_items
            WHERE sale_id = s.sale_id
            ORDER BY sale_item_id ASC
            LIMIT 1
        ) i ON TRUE
        LEFT JOIN {schema}.items it ON it.item_id = i.item_id
        WHERE s.sim_run_id = %s AND s.customer_partner_id = %s AND s.sale_date <= %s
        ORDER BY s.sale_date DESC, s.sale_id DESC
        LIMIT %s
        """
    ).format(schema=sql.Identifier(schema))
    return fetch_all(conn, statement, [sim_run_id, partner_id, as_of, limit])


def load_partner_totals(
    conn: Connection, *, sim_run_id: str, as_of: date, partner_id: str
) -> dict[str, object]:
    schema = get_db_schema()
    statement = sql.SQL(
        """
        SELECT COALESCE(SUM(total_amount_krw), 0) AS total_sales_krw,
               COUNT(*)::int AS sales_count,
               COALESCE(SUM(contribution_profit_krw), 0) AS contribution_profit_krw,
               MAX(sale_date) AS latest_sale_date
        FROM {}.sales
        WHERE sim_run_id = %s AND customer_partner_id = %s AND sale_date <= %s
        """
    ).format(sql.Identifier(schema))
    return fetch_all(conn, statement, [sim_run_id, partner_id, as_of])[0]


def load_partner_items(
    conn: Connection, *, sim_run_id: str, as_of: date, partner_id: str
) -> list[dict]:
    schema = get_db_schema()
    statement = sql.SQL(
        """
        SELECT i.item_id, it.item_name,
               COALESCE(SUM(i.quantity_kg), 0) AS quantity_kg,
               COALESCE(SUM(i.line_amount_krw), 0) AS sales_amount_krw,
               COALESCE(SUM(i.contribution_profit_krw), 0) AS contribution_profit_krw
        FROM {schema}.sale_items i
        JOIN {schema}.sales s ON s.sale_id = i.sale_id
        LEFT JOIN {schema}.items it ON it.item_id = i.item_id
        WHERE s.sim_run_id = %s AND s.customer_partner_id = %s AND s.sale_date <= %s
        GROUP BY i.item_id, it.item_name
        ORDER BY i.item_id ASC
        """
    ).format(schema=sql.Identifier(schema))
    return fetch_all(conn, statement, [sim_run_id, partner_id, as_of])


def load_partner_receivables(
    conn: Connection, *, sim_run_id: str, as_of: date, partner_id: str
) -> list[dict]:
    schema = get_db_schema()
    statement = (
        sql.SQL(
            """
        SELECT r.receivable_id, r.sale_id, r.due_date, r.original_amount_krw,
        """
        )
        + history_columns()
        + sql.SQL(
            """
        FROM {schema}.receivables r
        JOIN {schema}.sales s ON s.sale_id = r.sale_id AND s.sim_run_id = r.sim_run_id
        """
        ).format(schema=sql.Identifier(schema))
        + history_join(schema)
        + sql.SQL(
            """
        WHERE r.sim_run_id = %s AND s.customer_partner_id = %s AND r.issued_date <= %s
        ORDER BY r.due_date ASC, r.receivable_id ASC
        """
        )
    )
    #  ⚠️ `%s` 는 네 개다 — LATERAL 의 기준일이 WHERE 보다 **먼저** 온다.
    return fetch_all(conn, statement, [as_of, sim_run_id, partner_id, as_of])
