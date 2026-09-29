"""과거 시점(as_of) 사실 — Lot · Receipt · Pallet 자리 · 예약/할당 · Runtime 커버리지.

★ 2026-09-30 재구성 BL-015: `logistics/historical_repository.py` 에서 모델 · 상태 어휘 · 실패 종류를
  옮겼다. 읽기 조합은
  `readmodel/historical.py`, 상태 유도는 `domain/historical.py`, SQL 은 `repository/historical.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from app.logistics.schemas.turnover import LotTurnover

#: 용량 정책에 유효일 컬럼이 없다는 사실을 응답에 적는 값. **정책 이력 표를 만들지
#: 않는다** — 한계를 숨기지 않고 그대로 말하는 쪽을 고른다.
CAPACITY_BASIS_CURRENT_ACTIVE_POLICY = "CURRENT_ACTIVE_POLICY"

#: 유도되는 Lot 상태. 🔴 **`HOLD` 가 없다** — 그 상태를 쓰는 writer 가 하나도 없어
#: (`ledger.py` 도 잔량 0 에 `DEPLETED` 를 안 적는다) 되살릴 사건이 없다.
#: 없는 것을 만들지 않는다 — `inventory_lot_events` 는 이번 MVP 에서 만들지 않는다.
HistoricalLotState = Literal["ACTIVE", "DEPLETED", "DISPOSED"]

#: 유도되는 예약 상태. 🔴 **어휘가 둘뿐이다** — 그날 잡고 있었나 아닌가.
#: 할당 진행도(`RESERVED` · `PARTIALLY_ALLOCATED` · `ALLOCATED`)는 별개 축이라
#: `HistoricalReservation.status` 가 `reservation_status_for` 로 따로 유도한다.
HistoricalReservationState = Literal["HOLDING", "RELEASED"]

#: 유도되는 할당 상태. 🔴 **`inventory_allocations.status` 를 안 읽는다** — 그 칸은
#: 지금 값이라, 오늘 놓아준 예약의 할당이 **그 예약이 살아 있던 과거 날짜에도**
#: `CANCELLED` 로 보인다.
#:
#: ```text
#: SHIPPED    MOVE-OUT-{allocation_id} 의 moved_at <= as_of
#: RELEASED   그 예약의 released_as_of <= as_of        ← 놓아주면 할당도 함께 내려간다
#: ALLOCATED  그 밖 (decided_at < cutoff 로 이미 걸러졌다)
#: ```
#:
#: ⚠️ **`PICKED` 가 없다.** 그 상태로 쓰는 production writer 가 없어 되살릴 사건이
#:    없다 (`HistoricalLotState` 에 `HOLD` 가 없는 것과 같은 이유다).
#:
#: 🔴 **`CANCELLED` 를 따로 두지 않는다.** production 에서 할당이 취소되는 길은 둘뿐이고
#:    (`release_reservation` · FEFO 의 같은 판 안 재적합) 앞엣것은 `RELEASED` 로
#:    유도되며 뒤엣것은 **같은 날 안의 임시 상태**다 (WP-3 되살리기 날짜 경계가 그것을
#:    하루 안으로 묶는다 — `outbound._되살려도_되는_날인지_본다`).
HistoricalAllocationState = Literal["ALLOCATED", "SHIPPED", "RELEASED"]

#: 유도되는 Receipt 상태. 🔴 **`INSPECTING` · `CLOSED` 가 없다** — 그 둘은 사건이
#: 아니라 진행 표시라 되살릴 사실이 없다. Current 화면 어휘로 남는다.
HistoricalReceiptState = Literal["ARRIVED", "INSPECTED", "PUTAWAY_DONE"]


class AdjustMoveNotSupported(RuntimeError):
    """`ADJUST` 이동이 조회 범위에 있다. **부호를 모르므로 계산하지 않는다.**

    🔴 0 으로 치거나 `IN` 으로 넘겨짚지 않는다. 그렇게 그린 재고 곡선은
       틀렸다는 것조차 알려 주지 않는다. `ADJUST_IN` / `ADJUST_OUT` 로 갈리는 날
       (`23_inventory_move_type_split.sql`) 이 예외가 사라진다.
    """


@dataclass(frozen=True)
class HistoricalLot:
    """`as_of` 시점의 Lot 하나. **잔량도 상태도 원장에서 나온다.**"""

    lot_id: str
    item_id: str
    item_name: str | None
    #: `domain/grade.normalize_grade` 를 지난 값. Lot 생성 시 정해지는 정적 속성이라
    #: 원장이 아니라 행에서 읽어도 과거가 왜곡되지 않는다.
    grade: str | None
    storage_zone: str | None
    received_at: date
    #: 이 Lot 의 **실제 취득단가**(원/kg). 🔴 `grade` 와 같은 이유로 행에서 읽는다 —
    #: 입고 때 정해지고 그 뒤 바뀌는 경로가 없는 정적 속성이다.
    #: ★ 폐기 손실을 «지어내지 않고» 셈하는 유일한 근거다 (`agent.tools`).
    unit_cost_krw_per_kg: Decimal | None
    #: 🔴 **`IN − OUT − DISPOSE` 누계다.** `remaining_qty_kg` 컬럼이 아니다.
    remaining_qty_kg: Decimal
    state: HistoricalLotState
    #: 신선도·회전 파생값. `turnover` 가 정본이고 여기서 다시 계산하지 않는다 —
    #: 넣어 주는 것은 **그 시점 잔량**뿐이다.
    turnover: LotTurnover


@dataclass(frozen=True)
class HistoricalReceipt:
    """`as_of` 시점의 입고 Receipt 하나. **`receipt_status` 컬럼을 읽지 않는다.**"""

    receipt_id: str
    inbound_id: str | None
    item_id: str
    item_name: str | None
    arrived_at: date
    ordered_qty_kg: Decimal | None
    accepted_qty_kg: Decimal | None
    hold_qty_kg: Decimal | None
    rejected_qty_kg: Decimal | None
    fact_source: str
    state: HistoricalReceiptState
    inspection_id: str | None
    inspection_verdict: str | None
    inspected_qty_kg: Decimal | None
    lot_id: str | None
    in_move_id: str | None

    @property
    def stock_applied(self) -> bool:
        """Lot 과 원장 IN 이 **둘 다** `as_of` 까지 있어야 재고가 섰다고 본다."""
        return self.lot_id is not None and self.in_move_id is not None


@dataclass(frozen=True)
class HistoricalPalletPosition:
    """`as_of` 시점의 Pallet 자리. **`pallets.current_location_id` 를 안 읽는다.**

    ⚠️ **Pallet `status` 는 유도하지 않는다.** 사건 어휘(`CREATED` · `RELOCATED` ·
       `HOLD_MOVED` · `EMPTIED`)와 상태 어휘(`ACTIVE` · `HOLD` · `EMPTIED` ·
       `DISPOSED`)가 1:1 이 아니다 — `move_pallet` 은 상태를 그대로 두고 사건만
       적는다. 사건이 증명하는 것은 **자리**뿐이라 자리만 되살린다.
    """

    pallet_id: str
    lot_id: str
    location_id: str | None
    zone_id: str | None
    last_event_type: str
    occurred_at: datetime

    @property
    def occupies_position(self) -> bool:
        return self.location_id is not None


@dataclass(frozen=True)
class HistoricalCapacity:
    """창고 kg Capacity. 🔴 **정책에 유효일이 없어 과거로 되살릴 수 없다.**"""

    used_capacity_kg: Decimal
    guaranteed_capacity_kg: Decimal | None
    burst_capacity_kg: Decimal | None
    #: `CURRENT_ACTIVE_POLICY` — 지금 활성 정책을 그대로 썼다는 표시.
    capacity_basis: str = CAPACITY_BASIS_CURRENT_ACTIVE_POLICY


@dataclass(frozen=True)
class HistoricalAllocation:
    """`as_of` 시점의 할당 한 줄. **상태를 두 사건과 예약 해제일로 유도한다.**

    🔴 **`inventory_allocations.status` 를 읽지 않는다** (`HistoricalAllocationState`
       참조). 실측 9건이 전부 `SHIPPED` 인데, 그 값을 과거 화면에 실으면
       **할당만 서 있던 날에도 «출고 완료»** 로 보인다.
    """

    allocation_id: str
    reservation_id: str
    lot_id: str
    pallet_id: str | None
    allocated_qty_kg: Decimal
    allocation_basis: str
    decided_by: str
    decided_at: datetime
    #: 🔴 유도값이다. 저장된 `status` 가 아니다.
    state: HistoricalAllocationState
    #: 원장 OUT 이 나간 날. `state != "SHIPPED"` 면 `None` 이다.
    shipped_at: date | None
    note: str | None


@dataclass(frozen=True)
class HistoricalReservation:
    """`as_of` 시점의 예약 하나. **존재는 판매 확정일, 소멸은 `released_as_of` 다.**

    ```text
    존재    sales.order_date <= as_of     ← 확정된 날부터다 (예약은 확정 직후 선다)
    소멸    released_as_of <= as_of
    진행도  할당(decided_at · OUT Move)에서 유도
    ```

    🔴 **`inventory_reservations.status` 를 과거 정본으로 안 쓴다.** 그 칸은 **지금**
       값이라, 오늘 놓아준 예약이 과거 화면에서도 놓아준 것으로 보인다 —
       `remaining_qty_kg` 를 과거 잔량으로 쓰면 안 되는 것과 정확히 같은 잘못이다.

       ★ **딱 한 자리에서만 저장된 값을 쓴다** — `released_as_of <= as_of` 인 날의
         `RELEASED` / `CANCELLED` 구분이다. 놓아준 뒤에는 그 칸을 바꾸는 경로가 없어
         (`reserve_available_stock` · `allocate_stock` 둘 다 놓아준 예약을 거부한다)
         저장된 값이 곧 **놓아주던 날의 값**이다. 놓아주기 **전** 날짜로는 절대
         역류시키지 않는다.

    ⚠️ **`created_at` · `updated_at` 도 안 쓴다.** 벽시각이라 DB 를 손본 시각이지
       시뮬레이션 사실일이 아니다 (`released_as_of` 를 만든 이유가 그것이다).

    ⚠️ **`reserved_qty_kg` 는 «확보했던 양» 이지 «그날 잡고 있던 양» 이 아니다.**

    ```text
    확보했던 양   reserved_qty_kg      놓아준 뒤에도 보존된다 (WP-3 보정 2)
    그날 잡고 있었나  state             HOLDING / RELEASED
    그날 안 고른 몫   unallocated_qty_kg  놓아준 날부터 0 이다
    ```

       🔴 **`release_reservation` 이 이 값을 0 으로 덮지 않는다.** 덮으면
          놓아주기 **전** 날짜의 확보량이 함께 사라진다 — 01-10 에 60kg 을 잡고
          01-20 에 놓아준 예약을 01-15 로 조회하면 0kg 이 나오던 자리다.

       🔴 **날짜를 넘긴 top-up 은 재현할 수 없다 — 그리고 그 일이 날 수 있다.**
          확보량 변경 이력 표가 없어서다. `reserve_available_stock` 이
          `SET reserved_qty_kg = %s, updated_at = now()` 로 덮고, 남는 것은 벽시각뿐이다.

       ```text
       D    확정   sales_approval 이 예약을 세운다 · 모자라면 확보량이 요구량보다 작다
       D+1  출고   outbound_flow._ship_one 이 같은 예약을 다시 불러 못 채운 몫을 채운다
       조회         D 로 물어도 지금 행의 **최종 확보량**이 나온다   🔴 그날 값이 아니다
       ```

          ⚠️ **실측(2026-09-15)에서는 0건이다.** 판매가 물류가 준 판매가능량 안에서 주문을
             만들고 확정도 같은 날 같은 기준으로 잡아, 확정일에 모자란 예약이 2026-09-12
             이후 걷기에 하나도 없었다. 다만 **확정 예약 판정 기준일을 납품일로 옮기면서**
             (`master/sales_approval.py`) 판매 판단과 확정이 다른 날을 보게 됐고, 신선도
             절벽이 있는 날 모자란 예약이 날 수 있다.

          ★ 막는 길은 «날짜를 넘긴 채움 금지» 이고 이 판의 범위 밖이다. 이력 표도 만들지
            않는다 — **한계로 적어 두고**, 걷기에서 몇 건인지 세어 기록한다.
    """

    reservation_id: str
    sim_run_id: str
    item_id: str
    item_name: str | None
    sale_id: str | None
    #: 그 판매의 납품 기준일 (`sales.sale_date`). 🔴 **예약이 장부에 선 날이 아니다** —
    #: 그것은 확정일(`sales.order_date`)이고 존재 판정은 그 칸으로 한다.
    sale_date: date
    required_qty_kg: Decimal
    reserved_qty_kg: Decimal
    due_date: date | None
    #: 🔴 유도값이다. 저장된 `status` 가 아니다 — 그날 잡고 있었나 하나만 본다.
    state: HistoricalReservationState
    #: 🔴 유도값이다. DB 어휘(`ReservationStatus`)를 그대로 쓰되 그날 사실로 다시 센다.
    status: str
    released_as_of: date | None
    #: 그날 아직 창고에서 안 나간 할당의 합 (`state == "ALLOCATED"`).
    allocated_qty_kg: Decimal
    #: 그날 원장 OUT 으로 나간 몫 (`state == "SHIPPED"`).
    shipped_qty_kg: Decimal
    #: 그날 아직 Lot 을 안 고른 몫 = `reserved − (ALLOCATED + SHIPPED)`.
    #: 🔴 **놓아준 날부터 0 이다.** 확보량은 보존되지만 그날 이후로 «아직 붙일 것이
    #:    남았다» 는 사실은 없다 (`state == "RELEASED"`).
    unallocated_qty_kg: Decimal
    #: 그날 존재한 할당들. `decided_at < cutoff` 로 걸러진 것만 들어온다.
    allocations: tuple[HistoricalAllocation, ...]


class ReceiptLineageAmbiguous(RuntimeError):
    """한 Receipt 에 검수·Lot·원장 IN 이 **둘 이상** 붙어 있다. 무결성 위반이다.

    🔴 **하나를 고르지 않는다.** `inbound_inspections.receipt_id` 에도
       `inventory_lots.inbound_receipt_id` 에도 UNIQUE 가 없어 스키마상 둘이 설 수
       있고, `inspections.find_inspection` 은 그때 이미 멈춘다
       (`InspectionIntegrityError`). Historical Reader 만 JOIN 곱을 그대로 흘려
       **같은 Receipt 를 여러 줄로** 내보내면 깨진 계보가 정상 화면으로 그려진다.

    ⚠️ 앞 행이 뒤 행을 덮게 두는 것도 고르는 것이다 — 그래서 값이 아니라 예외다.
    """


@dataclass(frozen=True)
class RuntimeSnapshotCoverage:
    """그날 이 실행의 **Agent Runtime Snapshot 행이 있나** (`logistics_runtime_fixture`).

    🔴 **물리 사실(Lot · Move · Receipt)의 최소~최대 날짜로 대신 판정하지 않는다.**
       그 판정은 **양쪽으로** 틀린다 (실측 `SIM-BURNIN-202512` · 2026-09-09).

    ```text
    물리 사실 구간   2025-12-02 ~ 2026-09-12   285 일
    사실이 있는 날                              24 일   ← 구간의 8%
    스냅샷 행                                  254 일

    구간 안인데 스냅샷이 없는 날                31 일   ① 안 연 날을 «열렸다» 고 한다
      2025-12-02 ~ 2025-12-30  씨앗 구간 (원장 이동 215 건)
      2026-01-03 · 2026-01-04  스냅샷이 실제로 비어 있는 이틀
    스냅샷은 있는데 물리 사실이 없는 날         245 일   ② 열린 날을 «모른다» 고 한다
    ```

       ★ ② 가 특히 중요하다 — **입·출고가 0 건인 정상 하루는 Lot 도 Move 도
         Receipt 도 안 남긴다.** 물리 사실로는 그런 날을 증명할 방법이 **아예 없다.**

    ★ **축은 `(sim_run_id, as_of, usage_scope, is_active)` 다** —
      `uq_log_runtime_fixture` · `readmodel/current.get_active_logistics_runtime_fixture` 와
      같은 축이라 *"열렸다고 본 행"* 과 *"읽은 행"* 이 갈리지 않는다.

    ⚠️ `first_as_of` · `last_as_of` 는 **사람에게 보여 줄 맥락**일 뿐 판정 근거가
       아니다. 판정은 `has_snapshot` 하나가 하고, 그 값은 하루를 정확히 본다.
    """

    as_of: date
    #: 🔴 이 값 하나가 `NO_DATA` 판정이다. 구간 비교를 하지 않는다.
    has_snapshot: bool
    first_as_of: date | None
    last_as_of: date | None


@dataclass(frozen=True)
class LedgerLotState:
    """한 Lot 의 원장 요약. **잔량과 그 잔량의 관측일을 한 벌로 낸다.**

    ★ 둘을 따로 읽으면 두 번 질의하는 것이 아니라 **서로 다른 순간을 읽는** 것이 된다.
    """

    balance_kg: Decimal
    #: 🔴 그 Lot 의 **마지막 이동일** (`moved_at <= as_of` 중 가장 늦은 것).
    #: 잔량·상태가 *"언제부터 이 값이었나"* 에 답하는 유일한 업무 날짜다 —
    #: `created_at` 은 벽시각이라 못 쓴다 (이 모듈 첫머리의 Cutoff 규칙).
    last_moved_at: date
