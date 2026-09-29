"""출고 SQL — 가용 Lot · 미할당 예약 · 예약 · 할당 읽기와 예약 · 할당 쓰기.

★ 2026-09-30 재구성 BL-015: `logistics/outbound.py` 에서 옮겼다(흐름 안에 있던 INSERT · UPDATE 9문을
  함수로 뗐다 — 문면
  그대로). 받은 연결로 실행만 하고 commit 하지 않는다. 잠금은 부르는 쪽이 먼저 잡는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from psycopg import sql

from app.logistics.repository.rows import get_db_schema, named_rows
from app.logistics.schemas.outbound import AllocationBasis, ReservationStatus
from app.logistics.schemas.vocabulary import (
    ASSIGNED_ALLOCATION,
    HOLDING_ALLOCATION,
    HOLDING_RESERVATION,
)

_AMBIGUITY_PROBE_LIMIT = 2


# ── 가용재고 ────────────────────────────────────────────────────────────

_LOT_AVAILABILITY_COLUMNS = (
    "lot_id",
    "remaining_qty_kg",
    "received_at",
    "grade",
    "operational_limit_days",
    "medium_grade_factor",
    "held_qty_kg",
)


# ── 예약 ────────────────────────────────────────────────────────────────

_RESERVATION_COLUMNS = (
    "reservation_id",
    "sim_run_id",
    "item_id",
    "sale_id",
    "required_qty_kg",
    "reserved_qty_kg",
    "status",
    "due_date",
    #: 놓아준 시뮬레이션 날짜 (WP-3 M3). `NULL` 이면 아직 살아 있다.
    "released_as_of",
)


def select_reservation(conn: Any, *, reservation_id: str) -> dict | None:
    schema = sql.Identifier(get_db_schema())
    found = named_rows(
        conn,
        sql.SQL(
            """
            SELECT reservation_id, sim_run_id, item_id, sale_id,
                   required_qty_kg, reserved_qty_kg, status, due_date,
                   released_as_of
            FROM {}.inventory_reservations
            WHERE reservation_id = %s
            """
        ).format(schema),
        (reservation_id,),
        _RESERVATION_COLUMNS,
    )
    return found[0] if found else None


# ── 할당 ────────────────────────────────────────────────────────────────

_ALLOCATION_COLUMNS = (
    "allocation_id",
    "reservation_id",
    "lot_id",
    "allocated_qty_kg",
    "status",
    "allocation_basis",
    #: 이 결정이 선 시각. 🔴 **되살리기 날짜 경계가 이 값으로 선다** (WP-3).
    #: `created_at`(벽시각)이 아니다 — 호출자가 시뮬레이션 시간축으로 넣은 값이다.
    "decided_at",
)


def select_allocations(conn: Any, *, reservation_id: str) -> list[dict[str, Any]]:
    schema = sql.Identifier(get_db_schema())
    return named_rows(
        conn,
        sql.SQL(
            """
            SELECT allocation_id, reservation_id, lot_id, allocated_qty_kg, status,
                   allocation_basis, decided_at
            FROM {}.inventory_allocations
            WHERE reservation_id = %s
            ORDER BY allocation_id
            """
        ).format(schema),
        (reservation_id,),
        _ALLOCATION_COLUMNS,
    )


def select_available_lot_rows(
    conn: Any, *, sim_run_id: str, item_id: str, as_of: date
) -> list[dict[str, Any]]:
    """품목의 ACTIVE · 잔량 있는 · 그날까지 들어온 Lot 과 **아직 안 나간 할당 합**(`held_qty_kg`).

    ★ 판매 가용(신선도 소진 제외)으로 거르는 것은 부르는 쪽(`_available_lots`)이다.
    """
    schema = sql.Identifier(get_db_schema())
    query = sql.SQL(
        """
        SELECT l.lot_id, l.remaining_qty_kg, l.received_at, l.grade,
               p.operational_limit_days, p.medium_grade_factor,
               COALESCE((
                   SELECT SUM(a.allocated_qty_kg)
                   FROM {schema}.inventory_allocations a
                   JOIN {schema}.inventory_reservations r
                     ON r.reservation_id = a.reservation_id
                   WHERE a.lot_id = l.lot_id
                     AND a.status = ANY(%s)
                     AND r.status = ANY(%s)
               ), 0) AS held_qty_kg
        FROM {schema}.inventory_lots l
        JOIN {schema}.item_storage_policies p ON p.item_id = l.item_id
        WHERE l.sim_run_id = %s
          AND l.item_id = %s
          AND l.received_at <= %s
          AND l.status = 'ACTIVE'
          AND l.remaining_qty_kg > 0
        ORDER BY l.lot_id
        """
    ).format(schema=schema)
    #  ⚠️ **자리(`%s`)와 값의 개수·순서가 곧 계약이다.** 위 SQL 의 `%s` 는 나온
    #     차례대로 `a.status` · `r.status` · `l.sim_run_id` · `l.item_id` ·
    #     `l.received_at` 다섯이다. 조건을 더하거나 뺄 때 이 자리도 같이 고친다 —
    #     `received_at` 조건만 넣고 `as_of` 를 안 실어 `the query has 5
    #     placeholders but 4 parameters were passed` 로 출고 경로가 통째로
    #     멈춘 적이 있다 (#818).
    행들 = named_rows(
        conn,
        query,
        (
            sorted(HOLDING_ALLOCATION),
            sorted(HOLDING_RESERVATION),
            sim_run_id,
            item_id,
            as_of,
        ),
        _LOT_AVAILABILITY_COLUMNS,
    )
    return 행들


def select_unallocated_reservation_qty(conn: Any, *, sim_run_id: str, item_id: str) -> Decimal:
    """품목의 살아 있는 예약 중 **아직 Lot 을 안 고른 몫**의 합 (`item_free_stock_qty` 의 둘째 항).
    """
    schema = sql.Identifier(get_db_schema())
    query = sql.SQL(
        """
        SELECT COALESCE(SUM(GREATEST(r.reserved_qty_kg - COALESCE((
                   SELECT SUM(a.allocated_qty_kg)
                   FROM {schema}.inventory_allocations a
                   WHERE a.reservation_id = r.reservation_id
                     AND a.status = ANY(%(assigned)s)
               ), 0), 0)), 0) AS unallocated_reservations
        FROM {schema}.inventory_reservations r
        WHERE r.sim_run_id = %(sim)s AND r.item_id = %(item)s
          AND r.status = ANY(%(holding)s)
        """
    ).format(schema=schema)
    found = named_rows(
        conn,
        query,
        {
            "sim": sim_run_id,
            "item": item_id,
            "assigned": sorted(ASSIGNED_ALLOCATION),
            "holding": sorted(HOLDING_RESERVATION),
        },
        ("unallocated_reservations",),
    )
    미할당 = Decimal(found[0]["unallocated_reservations"])
    return 미할당


def insert_reservation(
    conn: Any,
    *,
    reservation_id: str,
    sim_run_id: str,
    item_id: str,
    sale_id: str | None,
    required_qty_kg: Decimal,
    reserved_qty_kg: Decimal,
    due_date: date | None,
) -> None:
    """예약 한 줄 INSERT (`RESERVED`). 잠금·가용량 재계산은 부르는 쪽이다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                INSERT INTO {}.inventory_reservations (
                    reservation_id, sim_run_id, item_id, sale_id,
                    required_qty_kg, reserved_qty_kg, due_date, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """
            ).format(schema),
            (
                reservation_id,
                sim_run_id,
                item_id,
                sale_id,
                required_qty_kg,
                reserved_qty_kg,
                due_date,
                "RESERVED",
            ),
        )


def update_reserved_qty(conn: Any, *, reservation_id: str, reserved_qty_kg: Decimal) -> None:
    """예약의 확보량(`reserved_qty_kg`) 하나만 바꾼다 — 재실행 top-up."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                UPDATE {}.inventory_reservations
                SET reserved_qty_kg = %s, updated_at = now()
                WHERE reservation_id = %s
                """
            ).format(schema),
            (reserved_qty_kg, reservation_id),
        )


def cancel_holding_allocations(conn: Any, *, reservation_id: str) -> None:
    """예약의 **아직 안 나간** 할당(`HOLDING_ALLOCATION`)을 전부 `CANCELLED` 로."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                UPDATE {}.inventory_allocations
                SET status = 'CANCELLED'
                WHERE reservation_id = %s AND status = ANY(%s)
                """
            ).format(schema),
            (reservation_id, sorted(HOLDING_ALLOCATION)),
        )


def mark_reservation_released(
    conn: Any, *, reservation_id: str, status: ReservationStatus, released_as_of: date
) -> None:
    """예약을 놓아준 상태(`RELEASED` · `CANCELLED`)와 그 시뮬레이션 날짜로. 확보량은 보존한다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                UPDATE {}.inventory_reservations
                SET status = %s, released_as_of = %s, updated_at = now()
                WHERE reservation_id = %s
                """
            ).format(schema),
            (status, released_as_of, reservation_id),
        )


def cancel_holding_allocation(conn: Any, *, allocation_id: str) -> None:
    """할당 하나를, 아직 안 나갔으면(`HOLDING_ALLOCATION`) `CANCELLED` 로."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                UPDATE {}.inventory_allocations
                SET status = 'CANCELLED'
                WHERE allocation_id = %s AND status = ANY(%s)
                """
            ).format(schema),
            (allocation_id, sorted(HOLDING_ALLOCATION)),
        )


def insert_allocation(
    conn: Any,
    *,
    allocation_id: str,
    reservation_id: str,
    lot_id: str,
    allocated_qty_kg: Decimal,
    allocation_basis: AllocationBasis,
    decided_by: str,
    decided_at: datetime,
) -> None:
    """새 할당 한 줄 INSERT (`ALLOCATED`). 잠금·가용량·상한 검사는 부르는 쪽이다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                INSERT INTO {}.inventory_allocations (
                    allocation_id, reservation_id, lot_id, allocated_qty_kg,
                    allocation_basis, decided_by, decided_at, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'ALLOCATED')
                """
            ).format(schema),
            (
                allocation_id,
                reservation_id,
                lot_id,
                allocated_qty_kg,
                allocation_basis,
                decided_by,
                decided_at,
            ),
        )


def revive_allocation(
    conn: Any,
    *,
    allocation_id: str,
    allocated_qty_kg: Decimal,
    allocation_basis: AllocationBasis,
    decided_by: str,
    decided_at: datetime,
) -> None:
    """취소됐던 할당을 같은 정체성으로 다시 `ALLOCATED` 로.

    되살려도 되는 날인지는 부르는 쪽이 본다.
    """
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                UPDATE {}.inventory_allocations
                SET allocated_qty_kg = %s, allocation_basis = %s,
                    decided_by = %s, decided_at = %s, status = 'ALLOCATED'
                WHERE allocation_id = %s AND status = 'CANCELLED'
                """
            ).format(schema),
            (allocated_qty_kg, allocation_basis, decided_by, decided_at, allocation_id),
        )


def update_reservation_status(conn: Any, *, reservation_id: str, status: ReservationStatus) -> None:
    """예약 상태 하나를 바꾼다 (할당 진행도 — `reservation_status_for`)."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                UPDATE {}.inventory_reservations
                SET status = %s, updated_at = now()
                WHERE reservation_id = %s
                """
            ).format(schema),
            (status, reservation_id),
        )


def mark_allocations_shipped(conn: Any, *, allocation_ids: Sequence[str]) -> None:
    """내보낸 할당들을 `SHIPPED` 로. 원장 OUT 은 부르는 쪽이 먼저 남긴다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                UPDATE {}.inventory_allocations
                SET status = 'SHIPPED'
                WHERE allocation_id = ANY(%s)
                """
            ).format(schema),
            (list(allocation_ids),),
        )
