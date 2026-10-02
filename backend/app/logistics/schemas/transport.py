"""운송 계약 · 차량 제원 · 계획 결과와 실패 종류.

계산은 `domain/transport.py`, SQL 은 `repository/transport.py`, 계획 순서는
`service/transport.py` 에 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


class TransportError(RuntimeError):
    """이 모듈이 내는 실패의 조상."""


class InvalidTransportRequest(TransportError, ValueError):
    """요청이 계약을 어긴다. DB 를 보기 전에 막는다."""


class RouteNotFound(TransportError, LookupError):
    """이 조건에 맞는 고정 Route 계약이 없다."""


class AmbiguousRoute(TransportError, LookupError):
    """고정 Route 계약이 둘 이상이다. 자동으로 하나를 고르지 않는다."""


class RateNotFound(TransportError, LookupError):
    """이 차량·거리에 해당하는 운임 구간이 없다. 값을 지어내지 않는다."""


class AmbiguousRate(TransportError, LookupError):
    """운임 구간이 겹친다. 싼 쪽·비싼 쪽을 임의로 고르지 않는다."""


class VehicleTooLargeToSplit(TransportError, ValueError):
    """가장 큰 차량으로도 못 싣는데 나눠 실을 수도 없다."""


@dataclass(frozen=True)
class VehicleSpec:
    """`vehicle_specs` 한 줄. 제원 그대로다."""

    vehicle_class: str
    body_type: str
    max_payload_kg: Decimal
    #: 차량 선택은 이 값으로 한다. 명목 최대적재량이 아니라 보수적 운영 Payload 다
    #: (DDL 주석: "명목 최대적재량이 아니라 보수적인 내부 운영 Payload").
    operational_payload_kg: Decimal
    max_pallet_floor_count: int | None


@dataclass(frozen=True)
class FixedRoute:
    """계약이 고정해 둔 운송 조건. 지도 경로가 아니라 계약 거리다."""

    logistics_contract_id: str
    distance_km: Decimal
    #: 계약의 차량 문자열. `vehicle_specs` 로 번역하지 않은 날것이다.
    contract_vehicle_class: str
    #: 계약이 적어 둔 회당 운임. 계산에는 안 쓰고 대조용으로만 돌려준다.
    contract_baseline_cost_krw: Decimal
    contract_status: str
    provisional: bool


@dataclass(frozen=True)
class TransportPlan:
    logistics_contract_id: str
    distance_km: Decimal
    vehicle_class: str
    body_type: str
    vehicle_operational_payload_kg: Decimal
    shipment_qty_kg: Decimal
    trip_count: int
    #: 회당 운임 (`vehicle_rate_table.base_rate_krw`).
    fixed_fee_per_trip_krw: Decimal
    estimated_cost_krw: Decimal
    #: 정본이 없다. 스키마 어디에도 소요시간 칸이 없어 항상 `None` 이다.
    standard_minutes: int | None
    #: 계약이 적어 둔 회당 운임. 운임표와 다를 수 있다 — 비용 계산은 운임표의
    #: `fixed_fee_per_trip_krw` 를 쓴다.
    contract_baseline_cost_krw: Decimal
    contract_vehicle_class: str
