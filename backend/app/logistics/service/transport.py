"""고정 Route 운송 계획 — 계약 → 차량 → 운임의 순서.

★ 2026-09-30 재구성 BL-015: `logistics/transport.py` 의 `plan_fixed_route_transport` 를 옮겼다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.logistics.domain.transport import select_vehicle, transport_quantity
from app.logistics.repository.transport import (
    load_vehicle_specs,
    resolve_fixed_route,
    select_fixed_fee,
)
from app.logistics.schemas.transport import RouteNotFound, TransportPlan


def plan_fixed_route_transport(
    conn: Any,
    *,
    shipment_qty_kg: Decimal,
    logistics_contract_id: str | None = None,
    body_type: str | None = None,
) -> TransportPlan:
    """계약 baseline 기반 운송 견적. **읽기와 계산만 한다 — 아무것도 쓰지 않는다.**

    ```text
    ① 계약 확정        거리 · 계약 baseline
    ② 차량 선택        실을 수 있는 가장 작은 차 · 넘치면 가장 큰 차로 나눈다
    ③ 운임 구간 조회    (차량, 차체, 거리) → 회당 운임
    ④ 비용 = 회당 운임 × trip 수
    ```

    🔴 **재고를 건드리지 않는다.** `remaining_qty_kg` · `inventory_moves` ·
       할당 상태 — 셋 다 그대로다. 실제 감소는 `outbound.ship_allocated_stock` 이 한다.

    ★ **결정론이다.** 같은 입력·같은 표면 같은 답이 나온다. 시계도 난수도 안 쓴다.

    ⚠️ `standard_minutes` 는 항상 `None` 이다 — 소요시간의 정본이 스키마에 없다.
       거리÷속도로 지어내지 않는다.

    :param body_type: 냉장이 필요한지는 **호출자가 정한다.** 안 주면 차체로 좁히지 않는다.
    :raises RouteNotFound: 계약이 없거나 계약에 거리가 없을 때.
    :raises AmbiguousRoute: 계약이 둘 이상일 때.
    :raises RateNotFound: 그 차량·거리의 운임 구간이 없을 때.
    :raises AmbiguousRate: 운임 구간이 겹칠 때.
    """
    수량 = transport_quantity(shipment_qty_kg, 칸="shipment_qty_kg")
    route = resolve_fixed_route(conn, logistics_contract_id=logistics_contract_id)
    specs = load_vehicle_specs(conn, body_type=body_type)
    if not specs:
        raise RouteNotFound(
            f"차량 제원이 없다 (body_type={body_type!r}). vehicle_specs 가 비어 있다."
        )
    vehicle, trips = select_vehicle(specs, shipment_qty_kg=수량, body_type=body_type)
    회당 = select_fixed_fee(
        conn,
        vehicle_class=vehicle.vehicle_class,
        body_type=vehicle.body_type,
        distance_km=route.distance_km,
    )
    return TransportPlan(
        logistics_contract_id=route.logistics_contract_id,
        distance_km=route.distance_km,
        vehicle_class=vehicle.vehicle_class,
        body_type=vehicle.body_type,
        vehicle_operational_payload_kg=vehicle.operational_payload_kg,
        shipment_qty_kg=수량,
        trip_count=trips,
        fixed_fee_per_trip_krw=회당,
        estimated_cost_krw=회당 * trips,
        standard_minutes=None,
        contract_baseline_cost_krw=route.contract_baseline_cost_krw,
        contract_vehicle_class=route.contract_vehicle_class,
    )
