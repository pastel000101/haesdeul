"""운송 SQL — 차량 제원 · 고정 Route 계약 · 운임 구간.

★ 2026-09-30 재구성 BL-015: `logistics/transport.py` 에서 옮겼다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from psycopg import sql

from app.logistics.repository.rows import cell, get_db_schema
from app.logistics.schemas.transport import (
    AmbiguousRate,
    AmbiguousRoute,
    FixedRoute,
    InvalidTransportRequest,
    RateNotFound,
    RouteNotFound,
    VehicleSpec,
)

_AMBIGUITY_PROBE_LIMIT = 2


def _require_text(값: Any, *, 칸: str) -> str:
    if not isinstance(값, str) or not 값.strip():
        raise InvalidTransportRequest(f"{칸} 가 비었다: {값!r}")
    return 값


# ── 읽기 ────────────────────────────────────────────────────────────────


def load_vehicle_specs(conn: Any, *, body_type: str | None = None) -> tuple[VehicleSpec, ...]:
    """차량 제원을 읽는다. **정본은 `vehicle_specs` 하나다.**"""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT vehicle_class, body_type, max_payload_kg,
                       operational_payload_kg, max_pallet_floor_count
                FROM {}.vehicle_specs
                WHERE (%s::text IS NULL OR body_type = %s)
                ORDER BY operational_payload_kg, vehicle_class
                """
            ).format(schema),
            (body_type, body_type),
        )
        행들 = list(cursor.fetchall())
    return tuple(
        VehicleSpec(
            vehicle_class=str(cell(행, 0, "vehicle_class")),
            body_type=str(cell(행, 1, "body_type")),
            max_payload_kg=Decimal(str(cell(행, 2, "max_payload_kg"))),
            operational_payload_kg=Decimal(str(cell(행, 3, "operational_payload_kg"))),
            max_pallet_floor_count=(
                None
                if cell(행, 4, "max_pallet_floor_count") is None
                else int(cell(행, 4, "max_pallet_floor_count"))
            ),
        )
        for 행 in 행들
    )


def resolve_fixed_route(conn: Any, *, logistics_contract_id: str | None = None) -> FixedRoute:
    """운송 조건을 고정해 둔 계약 하나를 확정한다. **0 / 1 / 2+ 를 셋 다 다르게 다룬다.**

    ```text
    0개    → RouteNotFound
    1개    → 그것을 쓴다
    2개+   → AmbiguousRoute       🔴 자동으로 고르지 않는다
    ```

    ★ `logistics_contract_id` 를 주면 그 줄만 본다 — 계약이 여럿이 되는 날, 어느 것을
      쓸지는 **호출자가 정한다.**

    ⚠️ **거리가 비어 있으면 계획을 세우지 않는다.** `delivery_distance_km` 는
       nullable 이고, 없는 거리로는 운임 구간을 고를 수 없다. 0 으로 보정하면 가장
       싼 구간이 조용히 선택된다.
    """
    schema = sql.Identifier(get_db_schema())
    if logistics_contract_id is not None:
        _require_text(logistics_contract_id, 칸="logistics_contract_id")
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT logistics_contract_id, delivery_distance_km, vehicle_class,
                       transport_cost_per_delivery_krw, contract_status, provisional
                FROM {}.logistics_contracts
                WHERE (%s::text IS NULL OR logistics_contract_id = %s)
                ORDER BY logistics_contract_id
                LIMIT %s
                """
            ).format(schema),
            (logistics_contract_id, logistics_contract_id, _AMBIGUITY_PROBE_LIMIT),
        )
        행들 = list(cursor.fetchall())
    if not 행들:
        raise RouteNotFound(
            f"고정 Route 계약이 없다 (logistics_contract_id={logistics_contract_id!r})."
            " 거리·운임의 정본이 없으면 계획을 세우지 않는다."
        )
    if len(행들) > 1:
        raise AmbiguousRoute(
            f"고정 Route 계약이 둘 이상이다 ({len(행들)}건 이상)."
            " 🔴 하나를 임의로 고르지 않는다 — 호출자가 logistics_contract_id 를 준다."
        )
    행 = 행들[0]
    거리 = cell(행, 1, "delivery_distance_km")
    계약id = str(cell(행, 0, "logistics_contract_id"))
    if 거리 is None:
        raise RouteNotFound(
            f"계약에 거리가 없다 (logistics_contract_id={계약id!r})."
            " 🔴 0 으로 보정하지 않는다 — 가장 싼 운임 구간이 조용히 잡힌다."
        )
    return FixedRoute(
        logistics_contract_id=계약id,
        distance_km=Decimal(str(거리)),
        contract_vehicle_class=str(cell(행, 2, "vehicle_class")),
        contract_baseline_cost_krw=Decimal(str(cell(행, 3, "transport_cost_per_delivery_krw"))),
        contract_status=str(cell(행, 4, "contract_status")),
        provisional=bool(cell(행, 5, "provisional")),
    )


def select_fixed_fee(
    conn: Any, *, vehicle_class: str, body_type: str, distance_km: Decimal
) -> Decimal:
    """이 차량·거리의 회당 운임. **구간표에서 읽는다.**

    ```text
    distance_from_km < distance_km <= distance_to_km
    ```

    ★ 경계가 *"초과 ~ 이하"* 인 것은 DDL 주석이 못박은 계약이다
      (*"(0,11] = 문서의 ~11km"*). 양쪽을 이하로 잡으면 경계 거리에서 두 구간이 겹친다.

    ⚠️ **`is_active` 인 구간만 본다.** 내린 운임표로 견적을 내지 않는다.

    🔴 거리×단가 같은 새 모델을 만들지 않는다.
    """
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT rate_id, base_rate_krw
                FROM {}.vehicle_rate_table
                WHERE vehicle_class = %s AND body_type = %s AND is_active
                  AND distance_from_km < %s AND %s <= distance_to_km
                ORDER BY rate_id
                LIMIT %s
                """
            ).format(schema),
            (vehicle_class, body_type, distance_km, distance_km, _AMBIGUITY_PROBE_LIMIT),
        )
        행들 = list(cursor.fetchall())
    if not 행들:
        raise RateNotFound(
            f"운임 구간이 없다 (vehicle_class={vehicle_class!r} · body_type={body_type!r}"
            f" · distance_km={distance_km})."
            " 🔴 가장 가까운 구간으로 대체하지 않는다 — 없는 값을 지어내는 것과 같다."
        )
    if len(행들) > 1:
        겹침 = [str(cell(행, 0, "rate_id")) for 행 in 행들]
        raise AmbiguousRate(
            f"운임 구간이 겹친다 (vehicle_class={vehicle_class!r} · distance_km={distance_km}):"
            f" {겹침} …. 🔴 싼 쪽·비싼 쪽을 임의로 고르지 않는다."
        )
    return Decimal(str(cell(행들[0], 1, "base_rate_krw")))
