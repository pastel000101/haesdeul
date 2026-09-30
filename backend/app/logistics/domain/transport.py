"""운송 계산 — 차량 선택 · 운행 횟수 · 수량 검사.

★ 2026-09-30 재구성 BL-015: `logistics/transport.py` 에서 옮겼다. DB 를 만지지 않는다.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

from app.logistics.schemas.transport import (
    InvalidTransportRequest,
    RouteNotFound,
    VehicleSpec,
    VehicleTooLargeToSplit,
)

# ── 순수 계산 ───────────────────────────────────────────────────────────


def trip_count_for(*, shipment_qty_kg: Decimal, payload_kg: Decimal) -> int:
    """몇 번 실어야 하나. 순수 계산이다.

    ```text
    ceil(shipment_qty_kg / payload_kg)
    ```

    ★ 올림이다 — 남은 100kg 을 두고 갈 수 없다.
    """
    수량 = transport_quantity(shipment_qty_kg, 칸="shipment_qty_kg")
    적재 = transport_quantity(payload_kg, 칸="payload_kg")
    return math.ceil(수량 / 적재)


def select_vehicle(
    specs: Any, *, shipment_qty_kg: Decimal, body_type: str | None = None
) -> tuple[VehicleSpec, int]:
    """**실을 수 있는 가장 작은 차량**과 필요한 trip 수. 순수 계산이다.

    ```text
    운영 Payload 800 · 1200 · 2000 일 때
    qty  900  → 1200 짜리 · 1 trip
    qty 1200  → 1200 짜리 · 1 trip      ← 경계는 "이하" 다
    qty 1201  → 2000 짜리 · 1 trip
    qty 5000  → 2000 짜리 · 3 trip      ← 가장 큰 차로 나눠 싣는다
    ```

    🔴 **한 대로 되면 큰 차를 부르지 않는다.** 운임이 차량 등급마다 다르므로 과대
       배차는 그대로 비용이다.

    ⚠️ **`operational_payload_kg` 로 고른다.** `max_payload_kg` 는 명목값이라 그걸로
       고르면 운영 한도를 넘겨 싣는 계획이 나온다.

    :param body_type: 주면 그 차체만 본다. 안 주면 전부 본다 — 🔴 냉장이 필요한지는
        물류가 정하지 않는다. 호출자가 안 정했으면 좁히지 않는다.
    """
    수량 = transport_quantity(shipment_qty_kg, 칸="shipment_qty_kg")
    후보 = [s for s in specs if body_type is None or s.body_type == body_type]
    if not 후보:
        raise RouteNotFound(
            f"차량 제원이 없다 (body_type={body_type!r})."
            " vehicle_specs 에 줄이 없으면 계획을 세우지 않는다."
        )
    # ★ 작은 순서로 본다. 동률이면 `vehicle_class` 로 갈라 **결정론**을 지킨다.
    순서 = sorted(후보, key=lambda s: (s.operational_payload_kg, s.vehicle_class))
    for spec in 순서:
        if spec.operational_payload_kg >= 수량:
            return spec, 1
    가장큰 = 순서[-1]
    trips = trip_count_for(shipment_qty_kg=수량, payload_kg=가장큰.operational_payload_kg)
    if trips < 1:
        raise VehicleTooLargeToSplit(
            f"나눠 실을 횟수를 셀 수 없다 (수량 {수량} · 최대 {가장큰.operational_payload_kg})."
        )
    return 가장큰, trips


def transport_quantity(값: Any, *, 칸: str) -> Decimal:
    """수량을 `Decimal` 로 좁힌다. **float 도 비유한값도 받지 않는다.**

    ★ `ledger._quantity` · `outbound._quantity` · `warehouse._quantity` 와 같은 규율이다.
    """
    if isinstance(값, bool) or not isinstance(값, Decimal):
        raise InvalidTransportRequest(
            f"{칸} 은 Decimal 이어야 한다 (받은 것: {값!r} · {type(값).__name__})."
        )
    if not 값.is_finite():
        raise InvalidTransportRequest(f"{칸} 이 유한한 수가 아니다: {값!r}")
    if 값 <= 0:
        raise InvalidTransportRequest(f"{칸} 은 0보다 커야 한다 (받은 것: {값})")
    return 값
