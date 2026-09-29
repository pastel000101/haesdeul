"""폐기 SQL — Lot 의 폐기 가능량 · 기존 폐기 Move · Lot 현재 잔량/상태 · DISPOSED 표시.

★ 2026-09-30 재구성 BL-015: `logistics/disposal.py` 에서 옮겼다. 받은 연결로 실행만 한다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from psycopg import sql

from app.logistics.repository.rows import cell, get_db_schema
from app.logistics.schemas.disposal import DisposalIntegrityError
from app.logistics.schemas.vocabulary import HOLDING_ALLOCATION, HOLDING_RESERVATION


def lot_disposable_qty(
    conn: Any, *, sim_run_id: str, lot_id: str
) -> tuple[Decimal, str]:
    """이 Lot 에서 **없애도 되는 양**과 현재 상태.

    ```text
    lot_disposable = remaining_qty_kg − 그 Lot 의 살아있는 할당 합
    ```

    ★ `outbound._available_lots` 의 Lot 축과 **같은 뜻**이다. 저쪽은 품목 전체를
      한 번에 훑는 목록이고 여기는 Lot 하나라, 같은 의미를 좁게 다시 적었다.
    """
    schema = sql.Identifier(get_db_schema())
    query = sql.SQL(
        """
        SELECT l.remaining_qty_kg, l.status,
               COALESCE((
                   SELECT SUM(a.allocated_qty_kg)
                   FROM {schema}.inventory_allocations a
                   JOIN {schema}.inventory_reservations r
                     ON r.reservation_id = a.reservation_id
                   WHERE a.lot_id = l.lot_id
                     AND a.status = ANY(%(alloc)s)
                     AND r.status = ANY(%(rsv)s)
               ), 0) AS held_qty_kg
        FROM {schema}.inventory_lots l
        WHERE l.sim_run_id = %(sim)s AND l.lot_id = %(lot)s
        """
    ).format(schema=schema)
    with conn.cursor() as cursor:
        cursor.execute(
            query,
            {
                "sim": sim_run_id,
                "lot": lot_id,
                "alloc": sorted(HOLDING_ALLOCATION),
                "rsv": sorted(HOLDING_RESERVATION),
            },
        )
        rows = cursor.fetchall()
    if not rows:
        raise DisposalIntegrityError(
            f"폐기할 Lot 이 없다 (sim_run_id={sim_run_id!r}, lot_id={lot_id!r})."
        )
    if len(rows) > 1:
        raise DisposalIntegrityError(f"같은 lot_id 가 둘 이상이다: {lot_id!r}")
    remaining = Decimal(cell(rows[0], 0, "remaining_qty_kg"))
    status = cell(rows[0], 1, "status")
    held = Decimal(cell(rows[0], 2, "held_qty_kg"))
    return remaining - held, status


def select_existing_disposal(conn: Any, *, move_id: str) -> dict[str, Any] | None:
    """같은 폐기 참조가 이미 적혀 있나. **읽기만 한다.**

    ★ **`note` 까지 읽는다.** 원장의 멱등 판정(`ledger._IDENTITY_COLUMNS`)이 `note` 를
      포함하므로 폐기 재실행도 같은 눈으로 봐야 한다 — 여기서 빼면 같은 참조에 다른
      설명이 붙어도 통과하고, 그 차이는 어디에도 안 남는다.
    """
    schema = sql.Identifier(get_db_schema())
    이름 = ("sim_run_id", "lot_id", "move_type", "quantity_kg", "moved_at", "reason_code", "note")
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT sim_run_id, lot_id, move_type, quantity_kg, moved_at,
                       reason_code, note
                FROM {}.inventory_moves
                WHERE move_id = %s
                """
            ).format(schema),
            (move_id,),
        )
        rows = cursor.fetchall()
    if not rows:
        return None
    return {name: cell(rows[0], index, name) for index, name in enumerate(이름)}


def mark_lot_disposed(conn: Any, *, sim_run_id: str, lot_id: str) -> None:
    """전량 폐기된 Lot 을 `DISPOSED` 로 넘긴다.

    🔴 **부분 폐기에는 붙이지 않는다.** 30kg 만 버린 Lot 은 여전히 살아 있는 재고다.

    ★ **`OUT` 으로 0 이 된 Lot 과 뜻이 다르다.** 저쪽은 팔려 나간 것이라 `ACTIVE` 로
      남고(원장은 상태를 안 바꾼다), 이쪽은 버려진 것이라 `DISPOSED` 다. 두 0 을 같은
      상태로 적으면 *"왜 없어졌나"* 를 나중에 구별할 수 없다.

    ⚠️ 수량은 건드리지 않는다 — 그것은 원장이 이미 했다.
    """
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                UPDATE {}.inventory_lots
                SET status = 'DISPOSED'
                WHERE sim_run_id = %s AND lot_id = %s AND remaining_qty_kg = 0
                """
            ).format(schema),
            (sim_run_id, lot_id),
        )


def select_lot_remaining_status(conn: Any, *, sim_run_id: str, lot_id: str) -> Any:
    """Lot 의 현재 잔량·상태 한 줄 `(remaining_qty_kg, status)` — 폐기 재실행이 되읽어 돌려준다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "SELECT remaining_qty_kg, status FROM {}.inventory_lots"
                " WHERE sim_run_id = %s AND lot_id = %s"
            ).format(schema),
            (sim_run_id, lot_id),
        )
        return cursor.fetchall()[0]
