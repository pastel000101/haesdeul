"""과거 시점 SQL — 원장 누계 · 그날 Lot · Receipt 계보 · Pallet 사건 · 날짜별 순증감 · Runtime
커버리지 · 예약/할당.

받은 연결로 읽기만 한다. 기준 시각(`cutoff`)은 부르는 쪽이 `domain/historical.timestamp_cutoff`
로 계산해 넘긴다.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from psycopg import sql

from app.logistics.repository.rows import dict_rows, schema_identifier
from app.logistics.schemas.vocabulary import USAGE_SCOPE

#: 원장 누계 한 조각. `ADJUST` 는 더하지 않고 세기만 한다 — 세어 둔 것을 보고
#: 호출부가 멈춘다.
#:
#: 잔량을 0 으로 만든 날(= 마지막 이동일)의 종류를 함께 센다. `lot_state` 가
#: «폐기로 비었나» 를 가르는 데 그 하루가 필요하다 — 총 폐기량만 보면 부분 폐기
#: 뒤 판매로 소진된 Lot 을 폐기된 Lot 과 구별할 수 없다(실측 2건).
#:
#: 그 하루를 날짜 자체로도 낸다(`last_moved_at`). 잔량은 파생 캐시라 자기 관측일이
#: 없고, "지금 500kg 이다" 를 언제부터 알 수 있었나에 답하는 것은 이 원장의 마지막
#: 이동일 하나다(`readmodel/observation.py` 의 `observe` · 상세설계 §18).
_LEDGER_AGGREGATE = sql.SQL(
    """
    SELECT m.lot_id,
           max(m.moved_at) AS last_moved_at,
           COALESCE(SUM(m.quantity_kg) FILTER (WHERE m.move_type = 'IN'), 0)
             - COALESCE(SUM(m.quantity_kg) FILTER (WHERE m.move_type IN ('OUT', 'DISPOSE')), 0)
               AS balance_kg,
           COALESCE(SUM(m.quantity_kg) FILTER (WHERE m.move_type = 'DISPOSE'), 0) AS disposed_kg,
           count(*) FILTER (WHERE m.move_type = 'ADJUST')::int AS adjust_count,
           count(*) FILTER (
               WHERE m.move_type = 'DISPOSE' AND m.moved_at = m.last_moved_on
           )::int AS dispose_on_last_day,
           count(*) FILTER (
               WHERE m.move_type = 'OUT' AND m.moved_at = m.last_moved_on
           )::int AS out_on_last_day
    FROM (
        SELECT mv.lot_id, mv.move_type, mv.quantity_kg, mv.moved_at,
               max(mv.moved_at) OVER (PARTITION BY mv.lot_id) AS last_moved_on
        FROM {schema}.inventory_moves mv
        WHERE mv.sim_run_id = %(sim)s
          AND mv.moved_at <= %(as_of)s
    ) m
    GROUP BY m.lot_id
    """
)


def select_allocation_rows_at(
    conn: Any,
    *,
    reservation_ids: list[str],
    as_of: date,
    cutoff: datetime,
) -> dict[str, tuple[dict[str, Any], ...]]:
    """그날 존재한 할당 행들을 예약별로 모은다. `status` 를 안 읽는다.

    원장 OUT 을 `LEFT JOIN` 으로 한 번에 붙인다. `move_id` 가 `MOVE-OUT-{allocation_id}`
    로 결정적이라 1:1 이고, 그래서 행이 불어나지 않는다(`receipt_state_at` 이 1:N 가능성
    때문에 곱을 막는 것과 다른 자리다).
    """
    schema = schema_identifier()
    rows = dict_rows(
        conn,
        sql.SQL(
            """
            SELECT al.allocation_id,
                   al.reservation_id,
                   al.lot_id,
                   al.pallet_id,
                   al.allocated_qty_kg,
                   al.allocation_basis,
                   al.decided_by,
                   al.decided_at,
                   al.note,
                   mv.moved_at AS shipped_at
            FROM {schema}.inventory_allocations al
            LEFT JOIN {schema}.inventory_moves mv
                   ON mv.move_id = 'MOVE-OUT-' || al.allocation_id
                  AND mv.move_type = 'OUT'
                  AND mv.moved_at <= %(as_of)s
            WHERE al.reservation_id = ANY(%(ids)s)
              AND al.decided_at < %(cutoff)s
            ORDER BY al.reservation_id, al.allocation_id
            """
        ).format(schema=schema),
        {"ids": reservation_ids, "as_of": as_of, "cutoff": cutoff},
    )
    모음: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        모음.setdefault(row["reservation_id"], []).append(row)
    return {키: tuple(값) for 키, 값 in 모음.items()}


def select_ledger_rows(conn: Any, *, sim_run_id: str, as_of: date) -> list[dict[str, Any]]:
    """Lot 별 `as_of` 까지의 원장 누계 행 (`_LEDGER_AGGREGATE`)."""
    rows = dict_rows(
        conn,
        _LEDGER_AGGREGATE.format(schema=schema_identifier()),
        {"sim": sim_run_id, "as_of": as_of},
    )
    return rows


def select_lot_rows_at(conn: Any, *, sim_run_id: str, as_of: date) -> list[dict[str, Any]]:
    """`as_of` 에 실재한 Lot 행 + 원장 누계 (`lot_state_at` 의 재료)."""
    schema = schema_identifier()
    rows = dict_rows(
        conn,
        sql.SQL(
            """
            SELECT l.lot_id, l.item_id, i.item_name, l.grade, l.storage_zone, l.received_at,
                   l.unit_cost_krw_per_kg,
                   sp.operational_limit_days, sp.medium_grade_factor,
                   tp.operational_turnover_target_days AS turnover_target_days,
                   tp.sell_priority_remaining_days,
                   COALESCE(mv.balance_kg, 0) AS balance_kg,
                   COALESCE(mv.disposed_kg, 0) AS disposed_kg,
                   COALESCE(mv.adjust_count, 0) AS adjust_count,
                   COALESCE(mv.dispose_on_last_day, 0) AS dispose_on_last_day,
                   COALESCE(mv.out_on_last_day, 0) AS out_on_last_day
            FROM {schema}.inventory_lots l
            JOIN {schema}.items i ON i.item_id = l.item_id
            LEFT JOIN {schema}.item_storage_policies sp ON sp.item_id = l.item_id
            LEFT JOIN {schema}.item_turnover_policies tp ON tp.item_id = l.item_id
            LEFT JOIN ({ledger}) mv ON mv.lot_id = l.lot_id
            WHERE l.sim_run_id = %(sim)s
              AND l.received_at <= %(as_of)s
            ORDER BY l.lot_id
            """
        ).format(schema=schema, ledger=_LEDGER_AGGREGATE.format(schema=schema)),
        {"sim": sim_run_id, "as_of": as_of},
    )
    return rows


def select_receipt_rows_at(
    conn: Any, *, sim_run_id: str, as_of: date, cutoff: datetime
) -> list[dict[str, Any]]:
    """`as_of` 까지 도착한 Receipt 행 + 그날까지의 검수·Lot·원장 IN 계보."""
    schema = schema_identifier()
    rows = dict_rows(
        conn,
        sql.SQL(
            """
            SELECT r.receipt_id, r.inbound_id, r.item_id, i.item_name, r.arrived_at,
                   r.ordered_qty_kg, r.accepted_qty_kg, r.hold_qty_kg, r.rejected_qty_kg,
                   r.fact_source,
                   ins.inspection_id, ins.verdict AS inspection_verdict, ins.inspected_qty_kg,
                   l.lot_id, mv.move_id AS in_move_id
            FROM {schema}.inbound_receipts r
            LEFT JOIN {schema}.items i ON i.item_id = r.item_id
            LEFT JOIN {schema}.inbound_inspections ins
                   ON ins.receipt_id = r.receipt_id
                  AND ins.inspected_at < %(cutoff)s
            LEFT JOIN {schema}.inventory_lots l
                   ON l.inbound_receipt_id = r.receipt_id
                  AND l.sim_run_id = r.sim_run_id
                  AND l.received_at <= %(as_of)s
            LEFT JOIN {schema}.inventory_moves mv
                   ON mv.lot_id = l.lot_id
                  AND mv.sim_run_id = r.sim_run_id
                  AND mv.move_type = 'IN'
                  AND mv.moved_at <= %(as_of)s
            WHERE r.sim_run_id = %(sim)s
              AND r.arrived_at <= %(as_of)s
            ORDER BY r.arrived_at DESC, r.receipt_id
            """
        ).format(schema=schema),
        {"sim": sim_run_id, "as_of": as_of, "cutoff": cutoff},
    )
    return rows


def select_pallet_rows_at(conn: Any, *, sim_run_id: str, cutoff: datetime) -> list[dict[str, Any]]:
    """Pallet 마다 `cutoff` 전 마지막 사건 한 줄 (`pallet_position_at` 의 재료)."""
    schema = schema_identifier()
    rows = dict_rows(
        conn,
        sql.SQL(
            """
            SELECT DISTINCT ON (p.pallet_id)
                   p.pallet_id, p.lot_id,
                   e.event_type, e.to_location_id, e.occurred_at,
                   sl.zone_id
            FROM {schema}.pallets p
            JOIN {schema}.inventory_lots l
              ON l.lot_id = p.lot_id
             AND l.sim_run_id = %(sim)s
            JOIN {schema}.pallet_events e
              ON e.pallet_id = p.pallet_id
             AND e.occurred_at < %(cutoff)s
            LEFT JOIN {schema}.storage_locations sl ON sl.location_id = e.to_location_id
            ORDER BY p.pallet_id, e.occurred_at DESC, e.pallet_event_id DESC
            """
        ).format(schema=schema),
        {"sim": sim_run_id, "cutoff": cutoff},
    )
    return rows


def select_net_moves_until(conn: Any, *, sim_run_id: str, end: date) -> list[dict[str, Any]]:
    """`end` 까지 날짜별 원장 순증감(IN − OUT − DISPOSE)과 ADJUST 건수."""
    schema = schema_identifier()
    rows = dict_rows(
        conn,
        sql.SQL(
            """
            SELECT m.moved_at,
                   COALESCE(SUM(m.quantity_kg) FILTER (WHERE m.move_type = 'IN'), 0)
                     - COALESCE(SUM(m.quantity_kg)
                                FILTER (WHERE m.move_type IN ('OUT', 'DISPOSE')), 0) AS net_kg,
                   count(*) FILTER (WHERE m.move_type = 'ADJUST')::int AS adjust_count
            FROM {schema}.inventory_moves m
            WHERE m.sim_run_id = %(sim)s
              AND m.moved_at <= %(end)s
            GROUP BY m.moved_at
            ORDER BY m.moved_at
            """
        ).format(schema=schema),
        {"sim": sim_run_id, "end": end},
    )
    return rows


def select_runtime_coverage_rows(
    conn: Any, *, sim_run_id: str, as_of: date
) -> list[dict[str, Any]]:
    """그 실행의 fixture 가 `as_of` 에 있나 · 연 구간의 처음과 끝."""
    schema = schema_identifier()
    rows = dict_rows(
        conn,
        sql.SQL(
            """
            SELECT
                bool_or(f.as_of = %(as_of)s) AS has_snapshot,
                min(f.as_of) AS first_as_of,
                max(f.as_of) AS last_as_of
            FROM {schema}.logistics_runtime_fixture f
            WHERE f.sim_run_id = %(sim)s
              AND f.usage_scope = %(scope)s
              AND f.is_active
            """
        ).format(schema=schema),
        {"sim": sim_run_id, "as_of": as_of, "scope": USAGE_SCOPE},
    )
    return rows


def select_snapshot_day_rows(
    conn: Any, *, sim_run_id: str, start: date, end: date
) -> list[dict[str, Any]]:
    """`start..end` 중 그 실행이 fixture 를 연 날들."""
    schema = schema_identifier()
    rows = dict_rows(
        conn,
        sql.SQL(
            """
            SELECT f.as_of
            FROM {schema}.logistics_runtime_fixture f
            WHERE f.sim_run_id = %(sim)s
              AND f.usage_scope = %(scope)s
              AND f.is_active
              AND f.as_of BETWEEN %(start)s AND %(end)s
            """
        ).format(schema=schema),
        {
            "sim": sim_run_id,
            "scope": USAGE_SCOPE,
            "start": start,
            "end": end,
        },
    )
    return rows


def select_reservation_rows_at(
    conn: Any, *, sim_run_id: str, as_of: date
) -> list[dict[str, Any]]:
    """`as_of` 에 존재한 예약 행 (`sales.order_date <= as_of` · 판매 없는 예약 제외)."""
    schema = schema_identifier()
    rows = dict_rows(
        conn,
        sql.SQL(
            """
            SELECT r.reservation_id,
                   r.sim_run_id,
                   r.item_id,
                   i.item_name,
                   r.sale_id,
                   s.sale_date,
                   r.required_qty_kg,
                   r.reserved_qty_kg,
                   r.due_date,
                   r.released_as_of,
                   r.status AS stored_status
            FROM {schema}.inventory_reservations r
            JOIN {schema}.sales s ON s.sale_id = r.sale_id
            LEFT JOIN {schema}.items i ON i.item_id = r.item_id
            WHERE r.sim_run_id = %(sim)s
              AND s.order_date <= %(as_of)s
            ORDER BY s.sale_date, r.reservation_id
            """
        ).format(schema=schema),
        {"sim": sim_run_id, "as_of": as_of},
    )
    return rows
