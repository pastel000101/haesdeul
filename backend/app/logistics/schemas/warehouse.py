"""Pallet · Zone 의 어휘 · 결과 · 실패 종류.

배치 순서는 `service/warehouse.py`, 값 검사는 `domain/warehouse.py`, SQL 은
`repository/warehouse.py`, 배치 잠금은 `repository/locks.py` 에 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

#: `ck_pallets_status` 어휘 그대로다.
PalletStatus = Literal["ACTIVE", "HOLD", "EMPTIED", "DISPOSED"]
#: `ck_pallet_events_type` 어휘 그대로다.
PalletEventType = Literal["CREATED", "PUTAWAY", "RELOCATED", "HOLD_MOVED", "EMPTIED"]

PALLET_EVENT_TYPES: frozenset[str] = frozenset(get_args(PalletEventType))

#: `move_pallet` 이 받는 어휘는 DB 어휘보다 좁다. 이벤트 vocabulary 와 함수
#: responsibility 는 다른 것이다.
#:
#: ```text
#: CREATED   place_lot_on_pallet 전용   — 자리 배정과 함께 나온다
#: EMPTIED   empty_pallet 전용          — 자리를 비우는 것은 이동이 아니다
#: PUTAWAY   경로가 없다               — 배치가 CREATED 로 한 번에 끝나서
#:                                       PUTAWAY 가 따로 뜻할 일이 아직 없다
#: ```
#:
#: 없는 뜻을 억지로 만들지 않는다. 검수 Zone → 보관 Zone 이동이 필요하면 그것은
#: `RELOCATED` 다.
MoveEventType = Literal["RELOCATED", "HOLD_MOVED"]

MOVE_EVENT_TYPES: frozenset[str] = frozenset(get_args(MoveEventType))


class WarehouseError(RuntimeError):
    """이 모듈이 내는 실패의 조상."""


class InvalidPlacementRequest(WarehouseError, ValueError):
    """요청이 DB 계약이나 Zone·Capacity 규칙을 어긴다. DML 전에 막는다."""


class ZonePolicyUnresolved(WarehouseError, ValueError):
    """이 품목의 허용 Zone 을 모른다.

    모른다는 것과 "아무 데나 된다" 는 다르다. 기본 Zone 을 지어내지 않는다.
    """


class PlacementConflict(WarehouseError, ValueError):
    """같은 `pallet_id` 에 다른 사실의 배치가 이미 있다. 조용히 덮지 않는다."""


class SpecItemMismatch(WarehouseError, ValueError):
    """건네받은 포장규격이 다른 품목의 것이다.

    FK 는 규격의 *존재*만 보장한다. `item_packaging_specs.item_id` 가 Lot 의 품목과
    같은지는 아무도 안 본다 — 그래서 배추 Lot 에 양파 규격이 붙을 수 있다.
    """


class PalletNotEmptyable(WarehouseError, ValueError):
    """이 Pallet 을 비워도 된다는 수량 근거가 없다.

    남은 물량을 추측해서 비우지 않는다. 비운 자리는 다른 Pallet 이 즉시 차지한다.
    """


class WarehouseIntegrityError(WarehouseError, ValueError):
    """배치 데이터가 스스로를 배반한다. 조용히 고치지 않는다."""


@dataclass(frozen=True)
class PlacementResult:
    applied: bool
    pallet_id: str
    location_id: str
    zone_id: str
    status: PalletStatus


@dataclass(frozen=True)
class EmptyResult:
    """Pallet 을 비운 결과. 수량은 여기 없다."""

    applied: bool
    pallet_id: str
    #: 비우기 전에 앉아 있던 자리. 비운 뒤에는 `NULL` 이라 결과로만 남는다.
    freed_location_id: str | None
    zone_id: str | None
    status: PalletStatus


@dataclass(frozen=True)
class LotPosition:
    """이 Lot 이 지금 앉아 있는 자리 한 줄."""

    pallet_id: str
    location_id: str
    zone_id: str
    status: PalletStatus


@dataclass(frozen=True)
class ZoneCapacity:
    """Zone 의 자리 사정. 단위는 Pallet Position 이다."""

    zone_id: str
    zone_kind: str
    #: `is_active` 인 자리 수. 정원이다.
    total_positions: int
    #: 그 자리에 앉은 `ACTIVE`·`HOLD` Pallet 수.
    occupied_positions: int
    free_positions: int
