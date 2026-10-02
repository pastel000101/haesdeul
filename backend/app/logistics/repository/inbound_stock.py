"""재고화 SQL — Receipt · 보관 Zone · 기존 Lot · 입고 Move 읽기, Lot INSERT, PUTAWAY_DONE, fixture
행 잠금.

받은 연결로 실행만 한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from psycopg import sql

from app.logistics.repository.rows import cell, get_db_schema
from app.logistics.schemas.inbound_stock import LotIntegrityError, ScheduleIntegrityError

#: 둘까지만 읽는다. 0 · 1 · 2+ 를 가르는 데 그 이상이 필요 없다.
_AMBIGUITY_PROBE_LIMIT = 2

_LOT_COLUMNS = (
    "lot_id",
    "sim_run_id",
    "purchase_item_id",
    "item_id",
    "grade",
    "received_at",
    "original_qty_kg",
    "unit_cost_krw_per_kg",
    "storage_zone",
    "status",
    "derivation_status",
    "inspection_status",
)


def _one_row(cursor: Any, 무엇: str) -> Any | None:
    """0 · 1 · 2+ 를 가른다. 첫 행을 고르지 않는다."""
    rows = cursor.fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        raise LotIntegrityError(f"{무엇} 이 둘 이상이다 — 어느 것이 진짜인지 여기서 고르지 않는다.")
    return rows[0]


def select_receipt_for_stock(conn: Any, *, receipt_id: str) -> dict[str, Any]:
    """Receipt 의 상태 · 도착일 · 검수 수량. PK 로 한 행을 읽는다."""
    schema = sql.Identifier(get_db_schema())
    이름 = (
        "receipt_status",
        "sim_run_id",
        "inbound_id",
        "purchase_item_id",
        "item_id",
        "arrived_at",
        "accepted_qty_kg",
        "hold_qty_kg",
        "rejected_qty_kg",
    )
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT receipt_status, sim_run_id, inbound_id, purchase_item_id, item_id,
                       arrived_at, accepted_qty_kg, hold_qty_kg, rejected_qty_kg
                FROM {}.inbound_receipts
                WHERE receipt_id = %s
                """
            ).format(schema),
            (receipt_id,),
        )
        row = _one_row(cursor, f"receipt_id={receipt_id!r} 인 Receipt")
    if row is None:
        raise LotIntegrityError(f"재고화할 Receipt 가 없다: receipt_id={receipt_id!r}")
    return {name: cell(row, index, name) for index, name in enumerate(이름)}


def select_storage_zone(conn: Any, *, item_id: str) -> str:
    """이 품목의 보관 Zone. `item_storage_policies` 가 주인이다.

    품목명으로 하드코딩하지 않는다. 기존 80 Lot 의 `storage_zone` 이 품목마다 이 표의
    값과 정확히 일치한다(실측) — 즉 여기가 그 칸의 권위 출처다.

    행이 없으면 멈춘다. 기본 Zone 을 고르면 그 추측이 로트의 보관 조건이 되고,
    신선도 계산(`operational_limit_days`)도 같은 표에서 오므로 함께 어긋난다.
    """
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL("SELECT storage_zone FROM {}.item_storage_policies WHERE item_id = %s").format(
                schema
            ),
            (item_id,),
        )
        row = _one_row(cursor, f"item_id={item_id!r} 의 보관 정책")
    if row is None:
        raise LotIntegrityError(
            f"보관 정책이 없어 storage_zone 을 정할 수 없다: item_id={item_id!r}."
            " 기본 Zone 을 고르지 않는다 — 그 추측이 로트의 보관 조건이 된다."
        )
    zone = cell(row, 0, "storage_zone")
    if not isinstance(zone, str) or not zone.strip():
        raise LotIntegrityError(f"item_storage_policies.storage_zone 을 읽을 수 없다: {zone!r}")
    return zone


def existing_lot(conn: Any, *, receipt_id: str) -> dict[str, Any] | None:
    """이 Receipt 로 이미 만든 Lot. `inbound_receipt_id` 가 그 연결이다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT lot_id, sim_run_id, purchase_item_id, item_id, grade, received_at,
                       original_qty_kg, unit_cost_krw_per_kg, storage_zone, status,
                       derivation_status, inspection_status
                FROM {}.inventory_lots
                WHERE inbound_receipt_id = %s
                ORDER BY lot_id
                LIMIT {}
                """
            ).format(schema, sql.Literal(_AMBIGUITY_PROBE_LIMIT)),
            (receipt_id,),
        )
        row = _one_row(cursor, f"receipt_id={receipt_id!r} 로 만든 Lot")
    if row is None:
        return None
    return {name: cell(row, index, name) for index, name in enumerate(_LOT_COLUMNS)}


def insert_lot(conn: Any, 값: Mapping[str, Any]) -> None:
    """accepted Lot 을 `remaining_qty_kg = 0` 으로 세운다.

    처음부터 `remaining = accepted` 로 넣고 IN 을 또 더하지 않는다. 그러면 잔량이 두 배가
    되고, 원장(`inventory_moves`)과 잔량이 어긋난 채로 남는다 — `ledger.py` 가 존재하는
    이유가 정확히 그것이다. 잔량을 바꾸는 것은 원장뿐이다.

    `derivation_status` 는 적지 않는다. 그 칸의 뜻이 "Burn-in Lot 이 어떤 파생규칙으로
    생성됐는지" 라, 실제로 도착한 Lot 은 NULL 이 맞다. 위치·팔레트도 적치 단계의 사실이라
    손대지 않는다.
    """
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                INSERT INTO {}.inventory_lots (
                    lot_id, sim_run_id, purchase_item_id, item_id, grade, received_at,
                    original_qty_kg, remaining_qty_kg, unit_cost_krw_per_kg,
                    storage_zone, status, inspection_status, inbound_receipt_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s, %s)
                """
            ).format(schema),
            (
                값["lot_id"],
                값["sim_run_id"],
                값["purchase_item_id"],
                값["item_id"],
                값["grade"],
                값["received_at"],
                값["original_qty_kg"],
                값["unit_cost_krw_per_kg"],
                값["storage_zone"],
                "ACTIVE",
                "PASS",
                값["inbound_receipt_id"],
            ),
        )


def select_in_move(conn: Any, *, move_id: str) -> dict[str, Any] | None:
    """`move_id` 의 원장 Move 한 줄 — 입고 Move 대조(`assert_existing_move`)의 재료.

    없으면 `None`.
    """
    schema = sql.Identifier(get_db_schema())
    이름 = (
        "sim_run_id",
        "lot_id",
        "move_type",
        "quantity_kg",
        "moved_at",
        "reason_code",
        "sale_item_id",
    )
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT sim_run_id, lot_id, move_type, quantity_kg, moved_at,
                       reason_code, sale_item_id
                FROM {}.inventory_moves
                WHERE move_id = %s
                """
            ).format(schema),
            (move_id,),
        )
        row = _one_row(cursor, f"move_id={move_id!r} 인 Move")
    if row is None:
        return None
    return {name: cell(row, index, name) for index, name in enumerate(이름)}


def mark_putaway_done(conn: Any, *, receipt_id: str) -> None:
    """Receipt 를 `PUTAWAY_DONE` 으로 넘긴다. 수량은 손대지 않는다.

    수량은 검수 단계가 이미 Receipt 에 적었고, 그것이 권위값이다.
    `CLOSED` 로 가지 않는다 — 보류·거부 정리는 여기서 하지 않는다.
    """
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                UPDATE {}.inbound_receipts
                SET receipt_status = %s, updated_at = now()
                WHERE receipt_id = %s
                """
            ).format(schema),
            ("PUTAWAY_DONE", receipt_id),
        )


# ── 일정 읽기·정리 ──────────────────────────────────────────────────────
#
# 도착 처리는 그날 fixture 행을 시작에서 잠그고(`service/inbound_stock.py` 의
# `load_in_transit_for_receiving`) 그 잠금 아래 끝까지 간다. 잠금 순서와 `None`/`[]`
# 구분은 그 함수가 정한다 — 여기는 잠금 SQL 만 둔다.


def lock_fixture_row_for_receiving(
    conn: Any, *, sim_run_id: str, as_of: date, usage_scope: str
) -> str:
    """그날 fixture 행을 잠그고 `in_transit_status` 하나만 읽는다.

    JSON 두 칸을 읽지 않는다(W3-3). 업무 일정의 정본은 `inbound_schedules` 이고, 이 행에서
    필요한 것은 "그 축을 확인했나" 하나뿐이다.

       승인 Writer 는 JSON 칸을 쓰지 않으므로(W3-3) 다음 상태가 성립한다.

       ```text
       in_transit_status  CONFIRMED     승인이 세운 값
       in_transit_json    NULL          아무도 안 고친 옛 값
       inbound_schedules  일정 있음
       ```

       이때 `in_transit_json IS NULL` 을 `UNRESOLVED` 로 읽으면, Console · Capacity 는
       status 를 보고 일정을 내는데 도착 처리만 `None` 을 내 같은 날 같은 입고가 화면에는
       있고 도착 처리에는 없는 상태가 된다. 판정 근거를 `status` 하나로 모아 그 갈림을
       없앤다.

    잠금 순서: `FOR UPDATE` 를 건다. 도착 처리는 이 행을 잠근 채 Receipt · 검수 · Lot ·
    원장 IN 까지 가고, 같은 행을 승인 전이(`service/transition.py` 의
    `persist_inventory`)가 status 로 건드린다. 순서(도착 전역 → 이 행 → 원장 전역)를
    바꾸지 않는다.
    """
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT in_transit_status
                FROM {}.logistics_runtime_fixture
                WHERE sim_run_id = %s AND as_of = %s AND usage_scope = %s
                FOR UPDATE
                """
            ).format(schema),
            (sim_run_id, as_of, usage_scope),
        )
        row = _one_row(cursor, "그날 runtime fixture 행")
    if row is None:
        raise ScheduleIntegrityError(
            f"도착 처리를 걸 물류 runtime fixture 행이 없다"
            f" (sim_run_id={sim_run_id}, as_of={as_of}, usage_scope={usage_scope})."
        )
    return str(cell(row, 0, "in_transit_status"))
