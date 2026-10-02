"""배치 SQL — 자리 · Pallet · Lot · 포장규격 · Zone 배정 · 점유 수 읽기와 Pallet · 사건 쓰기.

받은 연결로 실행만 한다.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from psycopg import sql

from app.logistics.repository.rows import cell, get_db_schema
from app.logistics.schemas.vocabulary import OCCUPYING_PALLET
from app.logistics.schemas.warehouse import LotPosition, WarehouseIntegrityError

_AMBIGUITY_PROBE_LIMIT = 2


def _scalar(cursor: Any, name: str) -> Any:
    """`count(*)` 처럼 반드시 한 줄 한 칸인 집계를 읽는다.

    집계는 0행이 나올 수 없어서 `_one` 의 0/1/2+ 규율을 적용할 자리가 아니다.
    """
    행 = cursor.fetchall()[0]
    return cell(행, 0, name)


def _one(rows: Any, *, 무엇: str) -> Any:
    """0/1/2+ 를 셋 다 다르게 다룬다. `fetchone()` 을 쓰지 않는다.

    첫 행을 집어오면 둘 이상인 것을 영영 모른다.
    """
    목록 = list(rows)
    if not 목록:
        return None
    if len(목록) > 1:
        raise WarehouseIntegrityError(
            f"{무엇} 이 둘 이상이다 ({len(목록)}건). 하나를 고르지 않는다."
        )
    return 목록[0]


# ── 읽기 ────────────────────────────────────────────────────────────────


def select_location(conn: Any, *, location_id: str) -> Any:
    """자리 한 줄 + 그 자리가 속한 Zone. 없으면 `None` 을 돌려준다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT sl.location_id, sl.zone_id, sl.is_active, sl.location_kind,
                       wz.zone_kind, wz.is_active AS zone_active
                FROM {}.storage_locations AS sl
                JOIN {}.warehouse_zones AS wz ON wz.zone_id = sl.zone_id
                WHERE sl.location_id = %s
                LIMIT %s
                """
            ).format(schema, schema),
            (location_id, _AMBIGUITY_PROBE_LIMIT),
        )
        return _one(cursor.fetchall(), 무엇=f"자리 {location_id!r}")


#: `select_pallet` 이 읽는 칸 — 행을 이 이름의 dict 로 편다(대조 · 흐름이 이름으로 읽는다).
_PALLET_COLUMNS = ("pallet_id", "lot_id", "packaging_spec_id", "current_location_id", "status")


def select_pallet(conn: Any, *, pallet_id: str) -> dict[str, Any] | None:
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT pallet_id, lot_id, packaging_spec_id, current_location_id, status
                FROM {}.pallets
                WHERE pallet_id = %s
                LIMIT %s
                """
            ).format(schema),
            (pallet_id, _AMBIGUITY_PROBE_LIMIT),
        )
        row = _one(cursor.fetchall(), 무엇=f"Pallet {pallet_id!r}")
    if row is None:
        return None
    return {name: cell(row, index, name) for index, name in enumerate(_PALLET_COLUMNS)}


def select_lot(conn: Any, *, sim_run_id: str, lot_id: str) -> Any:
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT lot_id, item_id, remaining_qty_kg, status
                FROM {}.inventory_lots
                WHERE lot_id = %s AND sim_run_id = %s
                LIMIT %s
                """
            ).format(schema),
            (lot_id, sim_run_id, _AMBIGUITY_PROBE_LIMIT),
        )
        return _one(cursor.fetchall(), 무엇=f"Lot {lot_id!r}")


def select_kg_per_pallet(conn: Any, *, item_id: str) -> Decimal | None:
    """이 품목의 kg → 자리 환산 단위. 정본은 `item_packaging_specs` 하나다.

    ```text
    None      환산 정본이 없다 (UNRESOLVED)   → 자리 수 상한을 못 센다
    Decimal   is_default 규격의 값
    ```

    기본 규격이 없으면 다른 규격을 아무거나 집지 않는다 — 부분 UNIQUE
    `uq_item_packaging_specs_default` 가 기본을 하나로 못박아 둔 이유가 그것이다.
    """
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT default_kg_per_pallet
                FROM {}.item_packaging_specs
                WHERE item_id = %s AND is_default
                LIMIT %s
                """
            ).format(schema),
            (item_id, _AMBIGUITY_PROBE_LIMIT),
        )
        행 = _one(cursor.fetchall(), 무엇=f"품목 {item_id!r} 의 기본 포장규격")
        return None if 행 is None else Decimal(str(cell(행, 0, "default_kg_per_pallet")))


def select_zone_allowed(
    conn: Any, *, item_id: str, zone_id: str
) -> bool | None:
    """이 품목을 이 Zone 에 둘 수 있나. 세 상태를 구분한다.

    ```text
    None    이 품목의 Zone 정책이 아예 없다   → UNRESOLVED. 추측하지 않는다
    False   정책이 있고 이 Zone 은 금지다
    True    정책이 있고 이 Zone 은 허용이다
    ```

    "정책 없음" 과 "금지" 를 같은 값으로 뭉개면, 정책을 안 만든 품목이
    "모든 Zone 금지" 로 보이거나 그 반대가 된다.
    """
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL("SELECT count(*) FROM {}.item_zone_assignments WHERE item_id = %s").format(
                schema
            ),
            (item_id,),
        )
        정책수 = _scalar(cursor, "count")
        if not 정책수:
            return None
        cursor.execute(
            sql.SQL(
                """
                SELECT allowed FROM {}.item_zone_assignments
                WHERE item_id = %s AND zone_id = %s
                LIMIT %s
                """
            ).format(schema),
            (item_id, zone_id, _AMBIGUITY_PROBE_LIMIT),
        )
        행 = _one(cursor.fetchall(), 무엇=f"품목 {item_id!r} · Zone {zone_id!r} 배정")
        # 정책은 있는데 이 Zone 줄이 없다 = 열거되지 않은 Zone = 금지다.
        return False if 행 is None else bool(cell(행, 0, "allowed"))


def count_occupying_pallets(
    conn: Any, *, lot_id: str, 제외: str | None = None
) -> int:
    """이 Lot 이 지금 차지한 자리 수. `제외` 는 재실행 중인 자기 Pallet 이다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT count(*) FROM {}.pallets
                WHERE lot_id = %s AND status = ANY(%s)
                  AND (%s::text IS NULL OR pallet_id <> %s)
                """
            ).format(schema),
            (lot_id, sorted(OCCUPYING_PALLET), 제외, 제외),
        )
        return int(_scalar(cursor, "count"))


def select_lot_positions(conn: Any, *, sim_run_id: str, lot_id: str) -> tuple[LotPosition, ...]:
    """이 Lot 이 지금 어디에 있나. 한 Lot 이 여러 자리에 나뉠 수 있다.

    스키마가 `1 Lot : N Pallet` 을 허용하고 `1 Pallet : 1 Lot` 만 막는다
    (`pallets.lot_id` 는 단일 값이다). 그래서 부분 Pallet 은 되고, 한 Pallet 에
    두 Lot 을 섞는 것은 안 된다. 관계를 임의로 넓히지 않는다.

    자리를 안 차지하는 `EMPTIED`·`DISPOSED` Pallet 은 빼고 돌려준다 — "지금 어디"
    를 묻는 질문이라 비운 Pallet 은 답이 아니다.
    """
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT p.pallet_id, p.current_location_id, sl.zone_id, p.status
                FROM {}.pallets AS p
                JOIN {}.inventory_lots AS il ON il.lot_id = p.lot_id
                JOIN {}.storage_locations AS sl ON sl.location_id = p.current_location_id
                WHERE p.lot_id = %s AND il.sim_run_id = %s AND p.status = ANY(%s)
                ORDER BY p.pallet_id
                """
            ).format(schema, schema, schema),
            (lot_id, sim_run_id, sorted(OCCUPYING_PALLET)),
        )
        행들 = list(cursor.fetchall())
    return tuple(
        LotPosition(
            pallet_id=str(cell(행, 0, "pallet_id")),
            location_id=str(cell(행, 1, "current_location_id")),
            zone_id=str(cell(행, 2, "zone_id")),
            status=str(cell(행, 3, "status")),  # type: ignore[arg-type]
        )
        for 행 in 행들
    )


def insert_pallet_event(
    conn: Any,
    *,
    pallet_id: str,
    event_type: str,
    from_location_id: str | None,
    to_location_id: str | None,
    occurred_at: datetime,
    recorded_by: str,
    note: str | None,
) -> None:
    """위치이동 이력 한 줄. 수량은 여기에 없다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                INSERT INTO {}.pallet_events (
                    pallet_id, event_type, from_location_id, to_location_id,
                    occurred_at, recorded_by, note
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """
            ).format(schema),
            (
                pallet_id,
                event_type,
                from_location_id,
                to_location_id,
                occurred_at,
                recorded_by,
                note,
            ),
        )


def select_zone_of_location(conn: Any, *, location_id: str) -> str | None:
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "SELECT zone_id FROM {}.storage_locations WHERE location_id = %s LIMIT %s"
            ).format(schema),
            (location_id, _AMBIGUITY_PROBE_LIMIT),
        )
        행 = _one(cursor.fetchall(), 무엇=f"자리 {location_id!r}")
        return None if 행 is None else str(cell(행, 0, "zone_id"))


def select_packaging_spec(conn: Any, *, packaging_spec_id: str) -> Any:
    """포장규격 한 줄 `(packaging_spec_id, item_id)` — 없으면 `None`, 둘 이상이면 멈춘다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT packaging_spec_id, item_id FROM {}.item_packaging_specs
                WHERE packaging_spec_id = %s
                LIMIT %s
                """
            ).format(schema),
            (packaging_spec_id, _AMBIGUITY_PROBE_LIMIT),
        )
        규격 = _one(cursor.fetchall(), 무엇=f"포장규격 {packaging_spec_id!r}")
    return 규격


def select_pallet_at_location(conn: Any, *, location_id: str) -> Any:
    """그 자리에 앉은 Pallet 한 줄 `(pallet_id,)` — 없으면 `None`."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT pallet_id FROM {}.pallets
                WHERE current_location_id = %s
                LIMIT %s
                """
            ).format(schema),
            (location_id, _AMBIGUITY_PROBE_LIMIT),
        )
        앉은것 = _one(cursor.fetchall(), 무엇=f"자리 {location_id!r} 의 Pallet")
    return 앉은것


def select_zone(conn: Any, *, zone_id: str) -> Any:
    """Zone 한 줄 `(zone_id, zone_kind)` — 없으면 `None`."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "SELECT zone_id, zone_kind FROM {}.warehouse_zones WHERE zone_id = %s LIMIT %s"
            ).format(schema),
            (zone_id, _AMBIGUITY_PROBE_LIMIT),
        )
        zone = _one(cursor.fetchall(), 무엇=f"Zone {zone_id!r}")
    return zone


def count_active_locations(conn: Any, *, zone_id: str) -> int:
    """Zone 의 열린 자리 수 (`storage_locations.is_active`)."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "SELECT count(*) FROM {}.storage_locations WHERE zone_id = %s AND is_active"
            ).format(schema),
            (zone_id,),
        )
        정원 = int(_scalar(cursor, "count"))
    return 정원


def count_zone_occupied(conn: Any, *, zone_id: str) -> int:
    """Zone 에 앉아 있는 Pallet 수 (`OCCUPYING_PALLET`)."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT count(*)
                FROM {}.pallets AS p
                JOIN {}.storage_locations AS sl ON sl.location_id = p.current_location_id
                WHERE sl.zone_id = %s AND p.status = ANY(%s)
                """
            ).format(schema, schema),
            (zone_id, sorted(OCCUPYING_PALLET)),
        )
        점유 = int(_scalar(cursor, "count"))
    return 점유


def insert_pallet(
    conn: Any,
    *,
    pallet_id: str,
    lot_id: str,
    packaging_spec_id: str | None,
    location_id: str,
    note: str | None,
) -> None:
    """새 Pallet 한 줄 (`ACTIVE`). 잠금·검사는 부르는 쪽이다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                INSERT INTO {}.pallets (
                    pallet_id, lot_id, packaging_spec_id, current_location_id, status, note
                ) VALUES (%s, %s, %s, %s, 'ACTIVE', %s)
                """
            ).format(schema),
            (pallet_id, lot_id, packaging_spec_id, location_id, note),
        )


def select_lot_item(conn: Any, *, lot_id: str) -> Any:
    """Lot 의 품목 한 줄 `(item_id,)` — 없으면 `None`."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL("SELECT item_id FROM {}.inventory_lots WHERE lot_id = %s LIMIT %s").format(
                schema
            ),
            (lot_id, _AMBIGUITY_PROBE_LIMIT),
        )
        lot = _one(cursor.fetchall(), 무엇=f"Lot {lot_id!r}")
    return lot


def update_pallet_location(conn: Any, *, pallet_id: str, to_location_id: str) -> None:
    """Pallet 의 현재 자리를 바꾼다. 잠금·검사는 부르는 쪽이다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL("UPDATE {}.pallets SET current_location_id = %s WHERE pallet_id = %s").format(
                schema
            ),
            (to_location_id, pallet_id),
        )


def select_lot_remaining(conn: Any, *, lot_id: str) -> Any:
    """Lot 의 잔량 한 줄 `(lot_id, remaining_qty_kg)` — 없으면 `None`."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "SELECT lot_id, remaining_qty_kg FROM {}.inventory_lots WHERE lot_id = %s LIMIT %s"
            ).format(schema),
            (lot_id, _AMBIGUITY_PROBE_LIMIT),
        )
        lot = _one(cursor.fetchall(), 무엇=f"Lot {lot_id!r}")
    return lot


def mark_pallet_emptied(conn: Any, *, pallet_id: str, occurred_at: datetime) -> None:
    """Pallet 을 `EMPTIED` 로, 자리를 비운다. 잠금·검사는 부르는 쪽이다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                UPDATE {}.pallets
                SET status = 'EMPTIED', current_location_id = NULL, emptied_at = %s
                WHERE pallet_id = %s
                """
            ).format(schema),
            (occurred_at, pallet_id),
        )
