"""회전 SQL — 품목 정책 한 벌 · 창고에 남은 Lot 의 회전 재료 행."""

from __future__ import annotations

from datetime import date
from typing import Any

from psycopg import sql

from app.logistics.repository.rows import cell, get_db_schema
from app.logistics.schemas.turnover import ItemPolicy

_LOT_TURNOVER_COLUMNS = (
    "lot_id",
    "item_id",
    "received_at",
    "remaining_qty_kg",
    "grade",
    "operational_limit_days",
    "medium_grade_factor",
    "turnover_target_days",
    "sell_priority_remaining_days",
)


def load_item_policy(conn: Any, *, item_id: str) -> ItemPolicy | None:
    """품목 하나의 정책. 품목 자체가 없으면 `None`.

    정책 두 표를 `LEFT JOIN` 한다. `INNER JOIN` 하면 정책이 없는 품목이 "그런 품목이
    없다" 로 보인다 — `load_lot_turnover` 가 같은 이유로 같은 조인을 쓴다. 없는 정책은
    없다고 답하는 것이 이 함수의 일이다.

    읽기만 한다. 커밋도 롤백도 안 하고, 없는 정책의 기본값을 지어내지 않는다.
    """
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT i.item_id, i.item_name,
                       sp.operational_limit_days, sp.medium_grade_factor,
                       tp.operational_turnover_target_days, tp.sell_priority_remaining_days
                FROM {schema}.items i
                LEFT JOIN {schema}.item_storage_policies sp ON sp.item_id = i.item_id
                LEFT JOIN {schema}.item_turnover_policies tp ON tp.item_id = i.item_id
                WHERE i.item_id = %(item_id)s
                """
            ).format(schema=schema),
            {"item_id": item_id},
        )
        rows = cursor.fetchall()
    if not rows:
        return None
    columns = (
        "item_id",
        "item_name",
        "operational_limit_days",
        "medium_grade_factor",
        "operational_turnover_target_days",
        "sell_priority_remaining_days",
    )
    row = {name: cell(rows[0], index, name) for index, name in enumerate(columns)}
    return ItemPolicy(
        item_id=row["item_id"],
        item_name=row["item_name"],
        operational_limit_days=row["operational_limit_days"],
        medium_grade_factor=row["medium_grade_factor"],
        operational_turnover_target_days=row["operational_turnover_target_days"],
        sell_priority_remaining_days=row["sell_priority_remaining_days"],
    )


def select_lot_turnover_rows(
    conn: Any, *, sim_run_id: str, as_of: date, lot_id: str | None = None
) -> list[dict[str, Any]]:
    """창고에 남아 있는 Lot 의 회전·신선도 계산 재료 행. 읽기만 한다.

    고르는 눈은 `readmodel/turnover.py` 의 `load_lot_turnover` 머리말과 같다 —
    `remaining_qty_kg > 0` · `received_at <= as_of`, 상태로 거르지 않고, 회전 정책 표는
    `LEFT JOIN`.
    """
    schema = sql.Identifier(get_db_schema())
    조건 = sql.SQL("AND l.lot_id = %(lot_id)s") if lot_id else sql.SQL("")
    query = sql.SQL(
        """
        SELECT l.lot_id, l.item_id, l.received_at, l.remaining_qty_kg, l.grade,
               sp.operational_limit_days, sp.medium_grade_factor,
               tp.operational_turnover_target_days AS turnover_target_days,
               tp.sell_priority_remaining_days
        FROM {schema}.inventory_lots l
        LEFT JOIN {schema}.item_storage_policies sp ON sp.item_id = l.item_id
        LEFT JOIN {schema}.item_turnover_policies tp ON tp.item_id = l.item_id
        WHERE l.sim_run_id = %(sim)s
          AND l.received_at <= %(as_of)s
          AND l.remaining_qty_kg > 0
          {조건}
        ORDER BY l.lot_id
        """
    ).format(schema=schema, 조건=조건)

    with conn.cursor() as cursor:
        cursor.execute(query, {"sim": sim_run_id, "as_of": as_of, "lot_id": lot_id})
        rows = cursor.fetchall()

    return [
        {name: cell(row, index, name) for index, name in enumerate(_LOT_TURNOVER_COLUMNS)}
        for row in rows
    ]
