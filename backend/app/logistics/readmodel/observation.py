"""Observe — 창고를 읽기만 한다. 판정도 계산식도 여기서 만들지 않는다.

```text
스냅샷 (readmodel/current.read_current_logistics)  잔량 · 상태 · 신선도 · 예약/할당 · 용량
회전   (readmodel/turnover.load_lot_turnover)      품목 ID · 판매우선 경계
원장   (readmodel/historical.ledger_state_by_lot)  잔량 대조 · 잔량의 관측일
표     (repository/exceptions.live_exceptions)     지금 살아 있는 Exception
```

원장을 읽는 이유가 둘이다. 하나는 캐시 대조이고, 다른 하나는 날짜다.
   `inventory_lots.remaining_qty_kg` 는 파생 캐시라 자기 관측일이 없다 — "지금
   500kg 이다" 를 언제부터 알 수 있었나에 답하는 것은 그 Lot 의 마지막 이동일뿐이다
   (상세설계 §18). 입고일로 메우면 D8 까지 나간 재고가 D1 부터 알던 사실이 된다.

재고 계산기를 다시 만들지 않는다. 신선도는 `domain/turnover.freshness_days_of`,
   미확정 물량은 `domain/tools.sellable_lot_contributions`, 창고 사용률은
   `domain/tools.calculate_window_capacity_usage` 가 낸 값 그대로 담는다 — 탐지기가
   4 Mode 회신과 다른 숫자를 보면 "조회는 괜찮다는데 Exception 은 위험하다" 가
   성립하고, 그때 사람이 믿을 값이 없다.

스냅샷은 따로 빌린 조회 연결로 읽는다(`readmodel/current.read_current_logistics`).
   이 함수의 `conn` 은 쓰기 트랜잭션의 것이다. 검사에서 갈아 끼울 수 있도록 `read_fn`
   으로 열어 둔다 — `master/service/maintenance.py` 의 `maintain_fn` 과 같은 규율이다.

관측일 도우미는 `domain/observation.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

from app.logistics.domain.observation import quantity_observed_as_of, status_observed_as_of
from app.logistics.domain.rules import (
    CAPACITY_TIGHT_POLICY_UNRESOLVED,
    FRESHNESS_PRESSURE_POLICY_UNRESOLVED,
)
from app.logistics.domain.tools import (
    calculate_window_capacity_usage,
    commitment_axes,
    sellable_lot_contributions,
)
from app.logistics.readmodel.current import read_current_logistics
from app.logistics.readmodel.historical import ledger_state_by_lot
from app.logistics.readmodel.turnover import load_lot_turnover
from app.logistics.repository.exceptions import live_exceptions
from app.logistics.schemas.current import LogisticsRead
from app.logistics.schemas.historical import AdjustMoveNotSupported, LedgerLotState
from app.logistics.schemas.monitoring import (
    CAPACITY_WINDOW_USAGE_UNRESOLVED,
    LEDGER_ADJUST_UNSUPPORTED,
    OUTBOUND_COMMITMENTS_UNRESOLVED,
    SNAPSHOT_AS_OF_MISMATCH,
    TURNOVER_LOT_UNRESOLVED,
    ObservationNotReady,
    ObservedCapacity,
    ObservedLot,
    ObservedPolicy,
    WarehouseObservation,
    derive_observed_as_of,
)


def observe(
    conn: Any,
    *,
    sim_run_id: str,
    as_of: date,
    read_fn: Callable[..., LogisticsRead] = read_current_logistics,
) -> WarehouseObservation:
    """그 실행 · 그날의 창고 상태 한 벌. 아무것도 바꾸지 않는다.

    :param conn: 쓰기 트랜잭션의 커넥션. 여기서는 읽기에만 쓴다 (회전 · 원장 ·
        Exception 표). 커밋도 롤백도 안 한다.
    :param read_fn: 스냅샷 경계. 기본값이 실제 함수 자체다 — `None` 을 안 받는다.
    """
    read = read_fn(as_of=as_of, sim_run_id=sim_run_id)
    snapshot = read.snapshot
    if snapshot.as_of != as_of:
        raise ObservationNotReady(
            f"{SNAPSHOT_AS_OF_MISMATCH}: 요청 {as_of} · 스냅샷 {snapshot.as_of}"
            " — 기준일이 섞인 관측으로 문제를 열지 않는다"
        )

    uncertainties: list[str] = []

    # ── 원장 축 — 잔량의 관측일이 여기서 온다 ────────────────────────────
    #
    # Lot 루프보다 먼저 읽는다. 잔량과 그 잔량의 날짜는 한 사실의 두 면이라
    # 따로 붙이면 서로 다른 순간을 읽게 된다.
    ledger_state, ledger_uncertainties = _ledger_state(conn, sim_run_id=sim_run_id, as_of=as_of)
    uncertainties.extend(ledger_uncertainties)

    # ── 예약·할당 축 — `build_inventory_by_item` 과 같은 눈 ───────────────
    #
    # `sellable_lot_contributions` 를 그대로 부른다. 품목 합계(판매 가능량)와
    # Lot 별 «아직 아무도 안 잡은 몫» 이 같은 함수에서 나와야, 매입에 나가는
    # 가용재고와 이 Exception 이 말하는 위험재고가 같은 재고를 가리킨다.
    axes = commitment_axes(snapshot)
    if axes is None:
        uncertainties.append(OUTBOUND_COMMITMENTS_UNRESOLVED)
        uncommitted_by_lot: dict[str, Decimal] = {}
    else:
        allocated_by_lot, _ = axes
        uncommitted_by_lot = {
            lot.lot_id: contribution
            for lot, contribution in sellable_lot_contributions(snapshot, allocated_by_lot)
        }

    # ── 회전 축 — 품목 ID 와 판매우선 경계 ───────────────────────────────
    #
    # 스냅샷은 품목 이름만 싣고(`InventoryLotSnapshot.item`), severity 가 쓰는
    # `sell_priority_remaining_days` 도 없다. 같은 WHERE(`잔량 > 0` ·
    # `received_at <= as_of`)를 쓰는 회전 조회를 그대로 빌린다.
    turnover_by_lot = {
        one.lot_id: one for one in load_lot_turnover(conn, sim_run_id=sim_run_id, as_of=as_of)
    }

    lots: list[ObservedLot] = []
    for lot in snapshot.on_hand_by_lot:
        turnover_row = turnover_by_lot.get(lot.lot_id)
        if turnover_row is None:
            uncertainties.append(f"{TURNOVER_LOT_UNRESOLVED}:{lot.lot_id}")
        last_moved_at, move_uncertainties = quantity_observed_as_of(ledger_state, lot=lot)
        uncertainties.extend(move_uncertainties)
        lots.append(
            ObservedLot(
                lot_id=lot.lot_id,
                item=lot.item,
                item_id=None if turnover_row is None else turnover_row.item_id,
                status=lot.status,
                received_at=lot.received_at,
                remaining_qty_kg=lot.available_qty_kg,
                # 판매 가용이 아닌 Lot 에는 이 값이 없다. 비-ACTIVE·신선도
                # 만료 Lot 은 애초에 팔 수 없어 «아직 안 잡힌 몫» 이 성립하지
                # 않는다 — 0 으로 적으면 «다 잡혔다» 로 읽힌다.
                uncommitted_kg=uncommitted_by_lot.get(lot.lot_id),
                remaining_freshness_days=lot.remaining_freshness_days,
                effective_freshness_limit_days=lot.effective_freshness_limit_days,
                sell_priority_remaining_days=(
                    None if turnover_row is None else turnover_row.sell_priority_remaining_days
                ),
                storage_zone=lot.storage_zone,
                remaining_qty_observed_as_of=last_moved_at,
                status_observed_as_of=status_observed_as_of(
                    lot.status, received_at=lot.received_at, last_moved_at=last_moved_at
                ),
            )
        )

    # ── 용량 축 ─────────────────────────────────────────────────────────
    usage = calculate_window_capacity_usage(snapshot, as_of)
    if usage is None:
        uncertainties.append(CAPACITY_WINDOW_USAGE_UNRESOLVED)
    if snapshot.capacity_tight_ratio is None:
        uncertainties.append(CAPACITY_TIGHT_POLICY_UNRESOLVED)
    if snapshot.freshness_pressure_ratio is None:
        uncertainties.append(FRESHNESS_PRESSURE_POLICY_UNRESOLVED)
    capacity = ObservedCapacity(
        used_kg=snapshot.used_capacity_kg,
        guaranteed_kg=snapshot.guaranteed_capacity_kg,
        burst_kg=snapshot.burst_capacity_kg,
        window_usage_ratio=usage,
    )
    policy = ObservedPolicy(
        freshness_pressure_ratio=snapshot.freshness_pressure_ratio,
        capacity_tight_ratio=snapshot.capacity_tight_ratio,
    )

    return WarehouseObservation(
        sim_run_id=sim_run_id,
        as_of=as_of,
        # 잔량 축만 센다. `used_capacity_kg` 가 이 Lot 들의 잔량 합이라
        # (`repository`) 그 사실의 관측일이 곧 이 값이다. 정책 축·예약 축은 여기
        # 안 든다 — 그 축들의 `None` 은 각 근거에서 따로 드러난다 (§18).
        inventory_observed_as_of=derive_observed_as_of(
            [one.remaining_qty_observed_as_of for one in lots]
        ),
        lots=tuple(lots),
        capacity=capacity,
        policy=policy,
        open_exceptions=live_exceptions(conn, sim_run_id=sim_run_id),
        uncertainties=tuple(dict.fromkeys(uncertainties)),
    )


def _ledger_state(
    conn: Any, *, sim_run_id: str, as_of: date
) -> tuple[dict[str, LedgerLotState] | None, list[str]]:
    """그날까지의 원장 한 벌. 못 읽으면 `None` 이고, 그때 잔량 관측일이 전부 없다.

    `ADJUST` 를 만나면 날짜도 함께 포기한다. 방향을 모르는 이동이 섞이면 그 Lot 의
    잔량 자체가 못 세는 값이 되고, 못 세는 값의 «언제부터» 는 더 못 댄다.
    """
    try:
        return ledger_state_by_lot(conn, sim_run_id=sim_run_id, as_of=as_of), []
    except AdjustMoveNotSupported:
        return None, [LEDGER_ADJUST_UNSUPPORTED]
