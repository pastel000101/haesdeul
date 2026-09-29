"""판매 원장 SQL — `sales` · `sale_items` 멱등 기록과 납품 완료 표시. **부르는 쪽 연결로.**

★ 2026-09-29 BL-013: `sales/persistence.py` 에서 SQL 을 옮겼다. 마스터가 트랜잭션을 가진
  연결을 넘기고(`sales_approval` · `outbound_flow`), 여기서는 commit · rollback 을 하지 않는다.
  같은 판매를 다시 적을 때 저장된 행이 계획과 같은지 되읽어 보는 것(`_assert_same_*`)은
  `ON CONFLICT DO NOTHING` 쓰기의 짝이라 SQL 과 함께 둔다.
"""

from typing import Any

from psycopg import Connection, sql

from app.core.settings import get_db_schema
from app.sales.schemas.sale_ledger import (
    SaleItemWrite,
    SalesPersistenceConflict,
    SaleWritePlan,
    SaleWriteResult,
)


def persist_sale(conn: Any, plan: SaleWritePlan) -> SaleWriteResult:
    """부르는 쪽 connection 으로 Sales header/item 을 멱등 저장한다."""

    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        item_id = _lookup_item_id(cursor, schema, plan.item_name)
        sale_item = plan.sale_item
        sale_item = SaleItemWrite(
            sale_item_id=sale_item.sale_item_id,
            sale_id=sale_item.sale_id,
            item_id=item_id,
            grade=sale_item.grade,
            quantity_kg=sale_item.quantity_kg,
            unit_price_krw_per_kg=sale_item.unit_price_krw_per_kg,
            line_amount_krw=sale_item.line_amount_krw,
            contribution_profit_krw=sale_item.contribution_profit_krw,
            contribution_margin_rate=sale_item.contribution_margin_rate,
        )
        header_written = _insert_sale(cursor, schema, plan)
        item_written = _insert_sale_item(cursor, schema, sale_item)
        if header_written == 0:
            _assert_same_sale(cursor, schema, plan)
        if item_written == 0:
            _assert_same_sale_item(cursor, schema, sale_item)
    return SaleWriteResult(
        sale_id=plan.sale_id,
        sale_item_id=sale_item.sale_item_id,
        item_id=sale_item.item_id,
        quantity_kg=sale_item.quantity_kg,
        sales_written=header_written,
        sale_items_written=item_written,
    )


def _lookup_item_id(cursor: Any, schema: sql.Identifier, item_name: str) -> str:
    cursor.execute(
        sql.SQL(
            """
            SELECT item_id
            FROM {}.items
            WHERE item_name = %s
            LIMIT 2
            """
        ).format(schema),
        [item_name],
    )
    rows = cursor.fetchall()
    if len(rows) != 1:
        raise SalesPersistenceConflict(f"item was not uniquely resolved: {item_name}")
    return str(_row_value(rows[0], "item_id", 0))


def _insert_sale(cursor: Any, schema: sql.Identifier, plan: SaleWritePlan) -> int:
    cursor.execute(
        sql.SQL(
            """
            INSERT INTO {}.sales (
                sale_id, sim_run_id, customer_partner_id, order_date, sale_date,
                collection_due_date, total_quantity_kg, total_amount_krw,
                contribution_profit_krw, collection_status, source_order_id, note,
                order_status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (sale_id) DO NOTHING
            """
        ).format(schema),
        [
            plan.sale_id,
            plan.sim_run_id,
            plan.customer_partner_id,
            plan.order_date,
            plan.sale_date,
            plan.collection_due_date,
            plan.total_quantity_kg,
            plan.total_amount_krw,
            plan.contribution_profit_krw,
            plan.collection_status,
            plan.source_order_id,
            plan.note,
            plan.order_status,
        ],
    )
    return int(cursor.rowcount)


def _insert_sale_item(cursor: Any, schema: sql.Identifier, sale_item: SaleItemWrite) -> int:
    cursor.execute(
        sql.SQL(
            """
            INSERT INTO {}.sale_items (
                sale_item_id, sale_id, item_id, grade, quantity_kg,
                unit_price_krw_per_kg, line_amount_krw, contribution_profit_krw,
                contribution_margin_rate
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (sale_item_id) DO NOTHING
            """
        ).format(schema),
        [
            sale_item.sale_item_id,
            sale_item.sale_id,
            sale_item.item_id,
            sale_item.grade,
            sale_item.quantity_kg,
            sale_item.unit_price_krw_per_kg,
            sale_item.line_amount_krw,
            sale_item.contribution_profit_krw,
            sale_item.contribution_margin_rate,
        ],
    )
    return int(cursor.rowcount)


def _assert_same_sale(cursor: Any, schema: sql.Identifier, plan: SaleWritePlan) -> None:
    cursor.execute(
        sql.SQL(
            """
            SELECT *
            FROM {}.sales
            WHERE sale_id = %s
            LIMIT 2
            """
        ).format(schema),
        [plan.sale_id],
    )
    rows = cursor.fetchall()
    if len(rows) != 1:
        raise SalesPersistenceConflict(f"sale was not found after insert conflict: {plan.sale_id}")
    row = rows[0]
    expected = {
        "sale_id": plan.sale_id,
        "sim_run_id": plan.sim_run_id,
        "customer_partner_id": plan.customer_partner_id,
        "order_date": plan.order_date,
        "sale_date": plan.sale_date,
        "collection_due_date": plan.collection_due_date,
        "total_quantity_kg": plan.total_quantity_kg,
        "total_amount_krw": plan.total_amount_krw,
        "contribution_profit_krw": plan.contribution_profit_krw,
        "collection_status": plan.collection_status,
        "source_order_id": plan.source_order_id,
        "note": plan.note,
    }
    for key, value in expected.items():
        if _row_value(row, key) != value:
            raise SalesPersistenceConflict(f"conflicting sale row for {plan.sale_id}: {key}")
    status = _row_value(row, "order_status")
    if status not in {"CONFIRMED", "READY", "DELIVERED"}:
        raise SalesPersistenceConflict(
            f"conflicting sale row for {plan.sale_id}: order_status"
        )


def _assert_same_sale_item(cursor: Any, schema: sql.Identifier, sale_item: SaleItemWrite) -> None:
    cursor.execute(
        sql.SQL(
            """
            SELECT *
            FROM {}.sale_items
            WHERE sale_item_id = %s
            LIMIT 2
            """
        ).format(schema),
        [sale_item.sale_item_id],
    )
    rows = cursor.fetchall()
    if len(rows) != 1:
        raise SalesPersistenceConflict(
            f"sale item was not found after insert conflict: {sale_item.sale_item_id}"
        )
    row = rows[0]
    expected = {
        "sale_item_id": sale_item.sale_item_id,
        "sale_id": sale_item.sale_id,
        "item_id": sale_item.item_id,
        "grade": sale_item.grade,
        "quantity_kg": sale_item.quantity_kg,
        "unit_price_krw_per_kg": sale_item.unit_price_krw_per_kg,
        "line_amount_krw": sale_item.line_amount_krw,
        "contribution_profit_krw": sale_item.contribution_profit_krw,
        "contribution_margin_rate": sale_item.contribution_margin_rate,
    }
    for key, value in expected.items():
        if _row_value(row, key) != value:
            raise SalesPersistenceConflict(
                f"conflicting sale item row for {sale_item.sale_item_id}: {key}"
            )


def _row_value(row: Any, name: str, index: int = 0) -> Any:
    if isinstance(row, dict):
        return row[name]
    return row[index]


def set_sale_delivered(conn: Connection, *, sale_id: str) -> int:
    """확정 · 준비 상태의 판매를 **납품 완료**로 바꾼다. 바뀐 행 수를 돌려준다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                UPDATE {}.sales
                SET order_status = 'DELIVERED'
                WHERE sale_id = %s
                  AND order_status IN ('CONFIRMED', 'READY')
                """
            ).format(schema),
            [sale_id],
        )
        return int(cursor.rowcount)


def sale_order_statuses(conn: Connection, *, sale_id: str) -> list[Any]:
    """그 판매의 주문 상태. **두 행까지만** 읽는다 — 하나가 아니면 부르는 쪽이 충돌로 본다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT order_status
                FROM {}.sales
                WHERE sale_id = %s
                LIMIT 2
                """
            ).format(schema),
            [sale_id],
        )
        rows = cursor.fetchall()
    return [_row_value(row, "order_status", 0) for row in rows]
