"""Zone 정원 읽기 — 받은 연결로 열린 자리 수와 점유 수를 센다."""

from __future__ import annotations

from typing import Any

from app.logistics.domain.warehouse import warehouse_text
from app.logistics.repository.rows import cell
from app.logistics.repository.warehouse import (
    count_active_locations,
    count_zone_occupied,
    select_lot_positions,
    select_zone,
)
from app.logistics.schemas.warehouse import (
    InvalidPlacementRequest,
    LotPosition,
    WarehouseIntegrityError,
    ZoneCapacity,
)


def get_zone_capacity(conn: Any, *, zone_id: str) -> ZoneCapacity:
    """Zone 의 자리 사정. kg 이 아니라 Pallet Position 으로 센다.

    ```text
    total    = is_active 자리 수
    occupied = 그 자리에 앉은 ACTIVE·HOLD Pallet 수
    ```

    판매 가용재고와 다른 축이다. 폐기대기 Lot 도, 예약·할당된 Lot 도 실제로
    창고에 있으면 자리를 차지한다. 자리가 비는 것은 원장 OUT·DISPOSE 뒤에 사람이
    Pallet 을 비웠을 때(`EMPTIED`)다.

    비활성 자리는 정원에서 뺀다. 하지만 거기 앉은 Pallet 은 점유로 센다 —
    자리를 닫았다고 물건이 사라지지는 않는다. 점유가 정원을 넘으면 조용히 0 으로
    깎지 않고 `WarehouseIntegrityError` 로 멈춘다.
    """
    warehouse_text(zone_id, 칸="zone_id")
    zone = select_zone(conn, zone_id=zone_id)
    if zone is None:
        raise InvalidPlacementRequest(f"없는 Zone 이다: {zone_id!r}")
    정원 = count_active_locations(conn, zone_id=zone_id)
    점유 = count_zone_occupied(conn, zone_id=zone_id)
    if 점유 > 정원:
        raise WarehouseIntegrityError(
            f"Zone 점유가 정원을 넘는다 (zone_id={zone_id!r}): 정원 {정원} · 점유 {점유}."
            " 자리를 닫았는데 Pallet 이 남아 있는지 확인한다."
        )
    return ZoneCapacity(
        zone_id=str(cell(zone, 0, "zone_id")),
        zone_kind=str(cell(zone, 1, "zone_kind")),
        total_positions=정원,
        occupied_positions=점유,
        free_positions=정원 - 점유,
    )


def get_lot_position(conn: Any, *, sim_run_id: str, lot_id: str) -> tuple[LotPosition, ...]:
    """이 Lot 이 지금 어디에 있나. 한 Lot 이 여러 자리에 나뉠 수 있다.

    스키마가 `1 Lot : N Pallet` 을 허용하고 `1 Pallet : 1 Lot` 만 막는다
    (`pallets.lot_id` 는 단일 값이다). 그래서 부분 Pallet 은 되고, 한 Pallet 에
    두 Lot 을 섞는 것은 안 된다. 관계를 임의로 넓히지 않는다.

    자리를 안 차지하는 `EMPTIED`·`DISPOSED` Pallet 은 빼고 돌려준다 — "지금 어디"
    를 묻는 질문이라 비운 Pallet 은 답이 아니다.

    입력 검사는 여기, SQL 은 `repository/warehouse.select_lot_positions`.
    """
    warehouse_text(sim_run_id, 칸="sim_run_id")
    warehouse_text(lot_id, 칸="lot_id")
    return select_lot_positions(conn, sim_run_id=sim_run_id, lot_id=lot_id)
