"""물류 화면 조회 응답 — 재고 · 입고 · 출고 콘솔과 FEFO 후보.

★ 2026-09-30 재구성 BL-015: `logistics/schemas.py` 의 화면 read model 절을 옮겼다(필드 · 모양
  그대로). 채우는 쪽은
  `readmodel/console.py` 다.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.logistics.schemas.monitoring import ExceptionRow, LiveExceptionsAt
from app.logistics.schemas.outbound import AllocationBasis, AllocationStatus, ReservationStatus
from app.logistics.schemas.snapshot import RuntimeSourceStatus
from app.logistics.schemas.turnover import TurnoverStatus

# ═══════════════════════════════════════════════════════════════════════════
# 화면 조회 read model (`readmodel/console` 이 만들고 `api/logistics/presenter.py` 가 읽는다)
#
# 🔴 **프론트까지 안 나간다.** `presenter.py` 가 이 값을 `Pane` · `Card` · `Stat` 로 옮겨
#    담아 `api/logistics/schema.LogisticsTab` 을 만든다. 그래서 이것은 화면 DTO 가
#    아니라 **물류 내부 read model** 이고, 도메인 쪽에 있어야 의존이 한 방향으로 선다
#    (2026-09-15 · 물류 문서 28).
#
# ```text
# api/logistics/presenter.py  →  readmodel/console.py  →  readmodel/historical · repository
# readmodel/console 결과       →  presenter.py 가 변환  →  api/logistics/schema.LogisticsTab
# ```
#
#    ★ 2026-09-30 재구성 BL-015 전에는 가운데가 `logistics/console_service.py`, 그 뒤가
#      `historical_repository` · `outbound` 였다.
#
#    ⚠️ 종전에는 `app/logistics/console_schemas.py` 라는 따로 선 파일이었다. 이름이
#       «console» 이라 화면 계약으로 읽혔고, 실제로 그 혼동에서 화면의 시간축이 섞였다.
# ═══════════════════════════════════════════════════════════════════════════

#: `available_qty_kg` 를 못 낸 이유. `tools.build_inventory_by_item` 이 `None` 을
#: 돌려주는 경로와 1:1 이다 — 어느 축을 못 읽었는지 화면이 알아야 한다.
#:
#: 🔴 **`CONFIRMED_OUTBOUND_*` 두 값이 WP-3 에서 빠졌다.** 판매가능량의 차감 축이
#:    예약·할당 한 벌로 좁혀져(이중 차감 제거) 확정 출고 축은 이 판정에 안 들어온다.
AvailableQtyUnresolvedReason = Literal[
    "OUTBOUND_COMMITMENTS_UNRESOLVED",
    #: 그날의 Agent Runtime Snapshot(`logistics_runtime_fixture`)이 없다. 재고 수량은
    #: 원장으로 되살아나지만 판매가능량은 그 스냅샷의 확정 출고 축이 있어야 선다.
    #: 🔴 없는 축을 0 으로 메우고 «다 팔 수 있다» 고 답하지 않는다.
    "RUNTIME_SNAPSHOT_UNAVAILABLE",
]

#: 이 칸의 값이 **어느 시간축**에서 나왔나. 전환기 표시다.
#:
#: ```text
#: HISTORICAL_AS_OF  요청한 as_of 시점 사실 (원장 · 사건 재생)
#: CURRENT_ROW       지금 행 값 — 되살릴 정본이 없거나, 그 값이 Runtime 축이다
#: ```
#:
#: 🔴 **둘을 한 숫자 안에 섞지 않는다.** 섞이면 «과거인 척하는 현재» 가 되고,
#:    그것이 이번 재설계가 없애려는 결함이다. 못 되살리는 축은 그렇다고 말한다.
TimeBasis = Literal["HISTORICAL_AS_OF", "CURRENT_ROW"]

class ConsoleModel(BaseModel):
    """콘솔 계약 공통 설정. 계약에 없는 칸을 조용히 흘리지 않는다."""

    model_config = ConfigDict(extra="forbid")


# ── 재고 ────────────────────────────────────────────────────────────────


class ConsoleInventoryItem(ConsoleModel):
    """품목 한 줄. **현재고와 판매가능량은 다른 값이다.**"""

    item_id: str
    item_name: str
    #: 물리 실재량. 만료 Lot 도 창고에 있으면 여기 들어간다.
    on_hand_qty_kg: Decimal
    #: 판매가능량 — `tools.build_inventory_by_item` 정본.
    #: 🔴 못 읽은 축이 있으면 `None` 이다. **0 으로 바꾸지 않는다.**
    available_qty_kg: Decimal | None
    #: 🔴 **지금 실제로 재고를 잡고 있는 양**이다 — 예약 행에 적힌 확보량의 합이 아니다.
    #:
    #:    ```text
    #:    reserved_qty_kg = allocated_qty_kg + unallocated_reserved_qty_kg
    #:    ```
    #:
    #:    항등식인 것이 계약이다. 전량 출고가 끝난 예약은 셋 다 0 이 된다
    #:    (`ship_allocated_stock` 이 예약 행의 `reserved_qty_kg` 를 안 줄이기 때문에
    #:    원래 값을 합하면 나간 재고를 아직 잡고 있는 것으로 보인다).
    #:
    #:    ⚠️ `ConsoleReservation.reserved_qty_kg` 와 **뜻이 다르다** — 저쪽은 예약
    #:       행에 적힌 DB 값 그대로다.
    reserved_qty_kg: Decimal
    #: 그 예약들이 **아직 안 내보낸** Lot 할당량 (ALLOCATED · PICKED).
    allocated_qty_kg: Decimal
    #: 잡아 뒀지만 아직 Lot 을 안 고른 몫. 이미 배정한 몫(SHIPPED 포함)을 뺀 값이다.
    unallocated_reserved_qty_kg: Decimal
    #: 🔴 **잡고 있는 양이 0 보다 큰 예약 수**다. 상태가 `ALLOCATED` 로 남아 있어도
    #:    전량 출고가 끝났으면 세지 않는다 — 기준은 상태 어휘가 아니라 수량이다.
    active_reservation_count: int
    #: `turnover.sell_priority` 가 참인 Lot 수. **폐기와 무관하다.**
    sell_priority_lot_count: int
    #: `remaining_freshness_days <= 0` 이며 잔량이 남은 Lot 수.
    expired_lot_count: int
    expired_qty_kg: Decimal
    #: `turnover.is_disposal_candidate` 가 참인 Lot 수. 만료 기준과 같은 판정이라
    #: `expired_lot_count` 와 같은 값이 나온다 — 두 이름을 화면이 함께 쓰기에 둘 다 싣는다.
    disposal_candidate_lot_count: int


class ConsoleInventoryLot(ConsoleModel):
    """Lot 한 줄. 파생값은 전부 `turnover.load_lot_turnover` 가 만든 것이다."""

    lot_id: str
    item_id: str
    item_name: str | None
    #: 🔴 `domain/grade.normalize_grade` 를 지난 값이다. 정규화표에 없는 raw 등급은
    #:    `None` 이 된다 — 임의 치환(`상품 → 상`)을 하지 않는다.
    grade: str | None
    #: 🔴 **`inventory_lots.remaining_qty_kg` 가 아니다.** `as_of` 까지의 원장
    #:    (`IN − OUT − DISPOSE`) 누계다. 그 컬럼은 Current Cache 라 과거를 못 말한다.
    remaining_qty_kg: Decimal
    received_at: date
    #: 🔴 **`inventory_lots.status` 컬럼이 아니라 유도값이다** (`ACTIVE` · `DEPLETED` ·
    #:    `DISPOSED`). `HOLD` 는 writer 가 없어 되살릴 사건이 없다 —
    #:    `schemas/historical.HistoricalLotState` 가 그 어휘의 주인이다.
    status: str | None
    #: 🔴 **`ConsoleZone.zone_id` 와 다른 어휘다. 조인하지 않는다.**
    #:    이 칸의 주인은 `item_storage_policies.storage_zone` 이고(실측 `COLD_HUMID_0_3`
    #:    계열), 창고 Zone 의 주인은 `warehouse_zones.zone_id` 다(실측
    #:    `HIGH_HUMIDITY_COLD` 계열). Lot 의 물리 위치는 `lot_locations[].zone_id` 로 본다.
    storage_zone: str | None
    remaining_freshness_days: int | None
    remaining_turnover_days: int | None
    #: 회전 정책이 없는 품목이면 `None`. **`NORMAL` 로 채우지 않는다.**
    turnover_status: TurnoverStatus | None
    sell_priority: bool
    disposal_candidate: bool


class ConsoleCapacity(ConsoleModel):
    """창고 kg Capacity. **Pallet Position 축과 다른 단위다.**"""

    #: 🔴 만료 Lot 도 잔량이 남아 있으면 여기 포함된다 — 판매불가 != 창고에서 사라짐.
    #: ★ `as_of` 시점 원장 합이다 — `remaining_qty_kg` 합이 아니다.
    used_capacity_kg: Decimal
    guaranteed_capacity_kg: Decimal | None
    burst_capacity_kg: Decimal | None
    #: 🔴 **한도 두 값은 과거로 되살린 것이 아니다.** `agent_policy_config` 에 유효일
    #:    컬럼이 없어 «그날 그 정책이었나» 를 알 수 없다. 유효일 컬럼을 새로 만들지
    #:    않기로 했으므로(`07 §15`) 지금 활성 정책을 쓰되 그 사실을 여기 적는다.
    capacity_basis: Literal["CURRENT_ACTIVE_POLICY"] = "CURRENT_ACTIVE_POLICY"


class ConsoleInventoryResponse(ConsoleModel):
    sim_run_id: str
    as_of: date
    items: list[ConsoleInventoryItem]
    lots: list[ConsoleInventoryLot]
    capacity: ConsoleCapacity
    #: `available_qty_kg` 가 전부 `None` 일 때만 채워진다. 그 외에는 `None`.
    available_qty_unresolved_reason: AvailableQtyUnresolvedReason | None = None
    #: 🔴 **`on_hand_qty_kg` 와 같은 시간축이다 (#760 · LOG-HIST-002).** 판매가능량도
    #:    이제 `as_of` 로 되살린다 — 화면(콘솔) 경로가 그날 Lot(`lot_state_at`)과 그날
    #:    예약(`reservation_state_at`)으로 세운 스냅샷을 정본 `tools.build_inventory_by_item`
    #:    에 먹인다. 종전에는 `repository.get_outbound_commitments`(현재 행)를 쓴 Runtime
    #:    스냅샷이라 «지금» 축이었다.
    #:
    #:    ⚠️ **Agent Runtime 경로는 그대로 «지금» 축이다.** 그쪽은 *"지금 더 팔 수
    #:       있나"* 를 묻는 자리라 현재 스냅샷을 쓴다(`ConsoleInventoryResponse` 를 안
    #:       거친다) — 바뀐 것은 화면(콘솔)뿐이다.
    available_qty_time_basis: TimeBasis = "HISTORICAL_AS_OF"
    #: 현재고 · Lot 상태 · 신선도 · 회전 · `used_capacity_kg` · 예약·판매가능량의 시간축.
    on_hand_time_basis: TimeBasis = "HISTORICAL_AS_OF"


# ── 입고 ────────────────────────────────────────────────────────────────


class ConsoleInTransitItem(ConsoleModel):
    inbound_id: str | None
    #: 🔴 마스터 규약이 이 값을 안 넘긴다 — `None` 이 **확정된 정상 상태**다
    #:    (`transition.build_next_inventory`). 그래서 도착일이 와도 `blocked` 로 갈린다.
    purchase_id: str | None
    item: str
    quantity_kg: Decimal
    expected_arrival_date: date | None


class ConsoleInboundReceipt(ConsoleModel):
    """Receipt + 검수 + 재고반영을 한 줄로 모은 것."""

    inbound_id: str | None
    receipt_id: str
    item_id: str
    item_name: str | None
    arrived_at: date
    #: 🔴 넷 다 nullable 이다. **NULL 을 0 으로 바꾸지 않는다** (DDL 주석).
    ordered_qty_kg: Decimal | None
    accepted_qty_kg: Decimal | None
    hold_qty_kg: Decimal | None
    rejected_qty_kg: Decimal | None
    #: 🔴 **`inbound_receipts.receipt_status` 컬럼이 아니라 유도값이다** (`ARRIVED` ·
    #:    `INSPECTED` · `PUTAWAY_DONE`). 근거는 사건 셋 — `arrived_at` ·
    #:    검수 `inspected_at` · 그 Receipt 의 Lot 과 원장 `IN`.
    #:    `INSPECTING` · `CLOSED` 는 사건이 아니라 진행 표시라 되살리지 않는다.
    receipt_status: str
    fact_source: str
    inspection_id: str | None
    inspection_verdict: str | None
    inspected_qty_kg: Decimal | None
    lot_id: str | None
    in_move_id: str | None
    #: Lot 과 원장 IN 이 **둘 다** 있을 때만 참.
    stock_applied: bool
    #: 그날까지 **수용 0 으로 재고 없이 입고 처리가 끝났나** (#805).
    #:
    #: 🔴 **이 판정을 여기서 만들지 않는다.** 정본은
    #:    `inbound_schedules.InboundScheduleView.settled_without_stock` 하나이고,
    #:    콘솔은 그 일정 한 벌에서 `inbound_id` 로 받아 적기만 한다. 같은 규칙을 두 벌
    #:    두면(예: 화면이 `accepted_qty_kg == 0` 으로 다시 판정) 경계에서 갈린다.
    #:
    #: 🔴 **`None` 은 «모른다» 다 — `False` 가 아니다.** 그날 입고 일정을 못 읽었으면
    #:    「반영 대기」인지 「반영할 재고 없음」인지 가릴 수 없다. 0 과 공란을 안 섞는
    #:    이 계약의 규율 그대로다.
    #:
    #: ★ `stock_applied` 와 **다른 사실이다.** 재고가 선 완료와 «만들 재고가 0 이라
    #:   끝난 완료» 는 둘 다 완료지만 재고는 한쪽에만 생긴다.
    settled_without_stock: bool | None = None


class ConsoleArrivalSummary(ConsoleModel):
    """`arrival.select_due_inbound` 의 네 갈래 건수.

    ⚠️ `due_count = 0` 이 정상일 수 있다 — `due` 는 `purchase_id` 까지 있어야 하는데
       마스터가 그 값을 안 넘기기로 확정했다. 도착일이 온 건은 `blocked` 로 나온다.
    """

    source_status: RuntimeSourceStatus
    due_count: int
    blocked_count: int
    not_due_count: int
    unresolved_count: int
    overdue_count: int


class ConsoleInboundResponse(ConsoleModel):
    sim_run_id: str
    as_of: date
    #: Receipt · 검수 · 재고반영의 시간축. 세 사건에서 되살린 값이다.
    receipt_time_basis: TimeBasis = "HISTORICAL_AS_OF"
    in_transit_status: RuntimeSourceStatus
    #: 🔴 `None`(미확인) 과 `[]`(0건 확인)은 다른 사실이다. `in_transit_status` 가 가른다.
    in_transit: list[ConsoleInTransitItem] | None
    receipts: list[ConsoleInboundReceipt]
    arrival_summary: ConsoleArrivalSummary


# ── 출고 ────────────────────────────────────────────────────────────────


class ConsoleAllocation(ConsoleModel):
    allocation_id: str
    lot_id: str
    pallet_id: str | None
    allocated_qty_kg: Decimal
    allocation_basis: AllocationBasis
    decided_by: str
    decided_at: datetime
    #: 🔴 **`as_of` 시점으로 유도한 값이다** — 저장된 `status` 컬럼이 아니다.
    #:    원장 OUT 이면 `SHIPPED`, 그 예약이 놓아준 뒤면 `CANCELLED`, 그 밖은
    #:    `ALLOCATED` 다 (`schemas/historical.HistoricalAllocationState`).
    status: AllocationStatus
    note: str | None


class ConsoleReservation(ConsoleModel):
    """예약 한 줄과 그 아래 할당들.

    🔴 **`allocated_qty_kg` 와 `unallocated_qty_kg` 의 분모가 다르다. 일부러다.**

    ```text
    allocated_qty_kg    ALLOCATED · PICKED           아직 창고에서 안 나간 몫
    unallocated_qty_kg  reserved − (ALLOCATED · PICKED · SHIPPED)
                                                     아직 Lot 을 안 고른 몫
    ```

       `SHIPPED` 를 앞에서는 빼고 뒤에서는 넣는다 — 나간 몫은 이미 원장 OUT 이
       잔량에서 덜어냈고(그래서 '잡고 있는 양'이 아니다), 그 예약이 더 이상 새로
       잡아 둘 필요도 없다. `outbound.item_free_stock_qty` 와 **같은 규율**이다.
    """

    reservation_id: str
    item_id: str
    item_name: str | None
    sale_id: str | None
    required_qty_kg: Decimal
    #: ⚠️ **«이 예약이 확보했던 양» 이다 — 그날 잡고 있던 양이 아니다.**
    #:    `ConsoleInventoryItem.reserved_qty_kg`(지금 잡고 있는 양)와 뜻이 다르다.
    #:
    #:    ```text
    #:    전량 출고 뒤    안 줄어든다     나간 것은 «확보했던» 사실을 안 지운다
    #:    놓아준 뒤        안 줄어든다     WP-3 보정 2 — 과거 확보량을 지우지 않는다
    #:    ```
    #:
    #:    🔴 그래서 이 값으로 *"지금/그날 몇 kg 잡고 있나"* 를 읽으면 안 된다.
    #:       그 물음의 답은 `status`(그날 유도값) · `allocated_qty_kg` ·
    #:       `unallocated_qty_kg` 다 — 놓아준 날부터 뒤의 둘은 0 이 된다.
    reserved_qty_kg: Decimal
    allocated_qty_kg: Decimal
    unallocated_qty_kg: Decimal
    due_date: date | None
    #: 🔴 **`as_of` 시점으로 유도한 값이다** — 저장된 `status` 컬럼이 아니다.
    #:    놓아준 뒤(`released_as_of <= as_of`)에만 저장된 `RELEASED`/`CANCELLED` 를
    #:    쓰고, 그 전 날짜에는 할당 진행도로 다시 센다
    #:    (`domain/historical.reservation_status_at`).
    status: ReservationStatus
    allocations: list[ConsoleAllocation]


class ConsoleOutboundResponse(ConsoleModel):
    sim_run_id: str
    as_of: date
    #: 0건이면 `[]` 다. **더미를 만들지 않는다.**
    reservations: list[ConsoleReservation]
    #: 🔴 **예약·할당 축을 `as_of` 로 되살린다 (WP-3).**
    #:
    #:    ```text
    #:    예약 존재   sales.order_date <= as_of        ← 확정일부터 (납품일이 아니다)
    #:    예약 소멸   released_as_of <= as_of              ← M3 가 세운 칸
    #:    할당 존재   decided_at < timestamp_cutoff(as_of)
    #:    출고        MOVE-OUT-{allocation_id} · moved_at <= as_of
    #:    ```
    #:
    #:    유도의 주인은 `readmodel/historical.reservation_state_at` 하나다.
    #:    저장된 `inventory_reservations.status` · `inventory_allocations.status`
    #:    는 **지금** 값이라 과거 정본으로 쓰지 않는다.
    #:
    #:    ⚠️ **`reserved_qty_kg` 만 지금 값이다.** 확보량 변경 이력이 없어서인데,
    #:       예약을 세우는 유일한 경로(마스터 `outbound_flow`)가 그 판매의 납품일
    #:       하루에만 돌아 날짜를 넘긴 top-up 이 production 에 없다. 그 전제가
    #:       깨지면 이 칸부터 다시 본다.
    reservation_time_basis: TimeBasis = "HISTORICAL_AS_OF"


class ConsoleFefoCandidate(ConsoleModel):
    lot_id: str
    #: 🔴 *"이 Lot 에서 아직 다른 할당에 안 묶인 물리량"* 이다.
    #:    **추가로 예약할 수 있는 양이 아니다** (`recommend_fefo_candidates` 주석).
    available_qty_kg: Decimal
    remaining_freshness_days: int | None
    received_at: date
    #: DB raw 등급 그대로다 — FEFO 후보는 정규화하지 않는다.
    grade: str | None


@dataclass(frozen=True)
class ConsolePage:
    """재고·물류 탭 한 판의 재료 — `readmodel/console.read_console_page` 가 채운다.

    그날 Runtime Snapshot 이 없으면 이 값 대신 판정(`RuntimeSnapshotCoverage`)만 온다 — 화면은
    그날을 0 이 아니라 «모르는 날» 로 그린다.
    """

    inventory: ConsoleInventoryResponse
    inbound: ConsoleInboundResponse
    outbound: ConsoleOutboundResponse
    live: LiveExceptionsAt
    resolved: tuple[ExceptionRow, ...]


@dataclass(frozen=True)
class StockChartFacts:
    """대시보드 재고 그래프 한 벌의 재료 — `readmodel/console.read_stock_chart` 가 채운다.

    그날 Runtime Snapshot 이 없으면 이 값 대신 판정(`RuntimeSnapshotCoverage`)만 온다.
    """

    #: `start`~`as_of` 각 날의 원장 누계(`onhand_total_by_day`).
    onhand_by_day: dict[date, Decimal]
    #: 그중 Runtime Snapshot 이 있는 날(`snapshot_days_between`).
    open_days: frozenset[date]
    #: 도착 표시(`in_transit`)를 꺼낼 입고 콘솔.
    inbound: ConsoleInboundResponse
