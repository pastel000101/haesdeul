"""판매 한 건의 흐름 SQL — **저장된 연결키로만 잇는다.**

★ 2026-09-29 BL-013: `sales/console_lifecycle.py` 에서 SQL 을 옮겼다. 어느 연결키가 무엇을
  잇는지와 단계 판정은 `readmodel/console_lifecycle.py` 머리말과 본문에 있다.
"""

from psycopg import Connection, sql

from app.core.settings import get_db_schema
from app.sales.repository._cursor import fetch_all


def load_sale(conn: Connection, *, sim_run_id: str, sale_id: str) -> dict[str, object] | None:
    schema = get_db_schema()
    statement = sql.SQL(
        """
        SELECT sale_id, sim_run_id, customer_partner_id, order_date, sale_date,
               collection_due_date, total_quantity_kg, total_amount_krw,
               contribution_profit_krw, collection_status, order_status, source_order_id
        FROM {}.sales WHERE sim_run_id = %s AND sale_id = %s
        """
    ).format(sql.Identifier(schema))
    found = fetch_all(conn, statement, [sim_run_id, sale_id])
    return found[0] if found else None


def load_outbound_moves(
    conn: Connection, *, sim_run_id: str, sale_id: str
) -> list[dict[str, object]]:
    """출고는 `sale_items.sale_item_id` 를 거쳐 붙는다 — 날짜로 잇지 않는다."""
    schema = get_db_schema()
    statement = sql.SQL(
        """
        SELECT m.move_id, m.moved_at, m.quantity_kg, m.lot_id, m.sale_item_id
        FROM {schema}.inventory_moves m
        JOIN {schema}.sale_items i ON i.sale_item_id = m.sale_item_id
        WHERE m.sim_run_id = %s AND i.sale_id = %s AND m.move_type = 'OUT'
        ORDER BY m.moved_at ASC, m.move_id ASC
        """
    ).format(schema=sql.Identifier(schema))
    return fetch_all(conn, statement, [sim_run_id, sale_id])


def load_reservations(
    conn: Connection, *, sim_run_id: str, sale_id: str
) -> list[dict[str, object]]:
    schema = get_db_schema()
    statement = sql.SQL(
        """
        SELECT reservation_id, status, reserved_qty_kg, required_qty_kg, created_at
        FROM {}.inventory_reservations
        WHERE sim_run_id = %s AND sale_id = %s
        ORDER BY reservation_id ASC
        """
    ).format(sql.Identifier(schema))
    return fetch_all(conn, statement, [sim_run_id, sale_id])


def load_receivables(conn: Connection, *, sim_run_id: str, sale_id: str) -> list[dict[str, object]]:
    schema = get_db_schema()
    statement = sql.SQL(
        """
        SELECT receivable_id, issued_date, due_date, original_amount_krw,
               received_amount_krw, outstanding_amount_krw, status
        FROM {}.receivables
        WHERE sim_run_id = %s AND sale_id = %s
        ORDER BY due_date ASC, receivable_id ASC
        """
    ).format(sql.Identifier(schema))
    return fetch_all(conn, statement, [sim_run_id, sale_id])


def load_collection_events(
    conn: Connection, *, sim_run_id: str, receivable_ids: list[str]
) -> list[dict[str, object]]:
    if not receivable_ids:
        return []
    schema = get_db_schema()
    statement = sql.SQL(
        """
        SELECT receivable_id, collection_date, target_received_total_krw
        FROM {}.master_collection_events
        WHERE sim_run_id = %s AND receivable_id = ANY(%s)
        ORDER BY collection_date ASC
        """
    ).format(sql.Identifier(schema))
    return fetch_all(conn, statement, [sim_run_id, receivable_ids])


def load_master_run(
    conn: Connection, *, sim_run_id: str, request_id: str
) -> dict[str, object] | None:
    """이 판매를 낳은 판매 사이클 실행. **업무 키로만 찾는다.**"""
    schema = get_db_schema()
    statement = sql.SQL(
        """
        SELECT run_id, end_code, runtime_status, created_at
        FROM {}.master_agent_runs
        WHERE sim_run_id = %s AND request_id = %s AND cycle = 'SALES'
        ORDER BY run_seq DESC, created_at DESC
        LIMIT 1
        """
    ).format(sql.Identifier(schema))
    found = fetch_all(conn, statement, [sim_run_id, request_id])
    return found[0] if found else None


def load_master_decision(conn: Connection, *, request_id: str) -> dict[str, object] | None:
    """그 판단의 승인 기록.

    ⚠️ `master_decisions` 에는 실행 축 칸이 없다. 업무 키가 실행 이름을 품고 있어
      (`REQ-DAILY-SALES-{실행}-…`) 키 자체가 축을 나르지만, 여기서 **이름을 쪼개
      뜻을 읽지 않는다** — 위 `load_master_run` 이 이미 실행 축으로 걸렀고, 이 조회는
      그 판단에 붙은 결정을 가져오는 것뿐이다.
    """
    schema = get_db_schema()
    statement = sql.SQL(
        """
        SELECT decision_id, decision, scenario_label, end_code_at_decision,
               decided_by, created_at
        FROM {}.master_decisions
        WHERE request_id = %s
        ORDER BY decision_seq DESC
        LIMIT 1
        """
    ).format(sql.Identifier(schema))
    found = fetch_all(conn, statement, [request_id])
    return found[0] if found else None
