"""재고 원장 재생용 SQL. 부르는 쪽의 연결로 읽는다."""

from datetime import date
from typing import Any

from psycopg import sql

from app.core.settings import get_db_schema


def select_inventory_ledger(conn: Any, *, sim_run_id: str, as_of: date) -> list:
    """Lot 원가와 ``as_of`` 까지의 재고 이동을 Lot · 시각 순으로 읽는다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT
                    lot.lot_id,
                    lot.unit_cost_krw_per_kg,
                    move.move_type,
                    move.quantity_kg
                FROM {schema}.inventory_lots lot
                LEFT JOIN {schema}.inventory_moves move
                  ON move.lot_id = lot.lot_id
                 AND move.sim_run_id = lot.sim_run_id
                 AND move.moved_at <= %(as_of)s
                WHERE lot.sim_run_id = %(sim_run_id)s
                  AND lot.received_at <= %(as_of)s
                ORDER BY lot.lot_id, move.moved_at, move.move_id
                """
            ).format(schema=schema),
            {"sim_run_id": sim_run_id, "as_of": as_of},
        )
        return cursor.fetchall()
