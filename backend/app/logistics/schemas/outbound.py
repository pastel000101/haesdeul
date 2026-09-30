"""출고(예약 · 할당 · 실출고)의 어휘 · 요청 · 결과 · 실패 종류.

★ 2026-09-30 재구성 BL-015: `logistics/outbound.py` 에서 옮겼다. 업무 순서는 `service/outbound.py`,
  판정 · ID 규칙은
  `domain/outbound.py`, SQL 은 `repository/outbound.py`, 잠금은 `repository/locks.py`. 붙잡은 상태
  집합은 `schemas/vocabulary.py`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal, get_args

from app.logistics.schemas.vocabulary import HOLDING_RESERVATION

#: `ck_inventory_reservations_status` 어휘 그대로다.
ReservationStatus = Literal["RESERVED", "PARTIALLY_ALLOCATED", "ALLOCATED", "RELEASED", "CANCELLED"]
#: `ck_inventory_allocations_status` 어휘 그대로다.
AllocationStatus = Literal["ALLOCATED", "PICKED", "SHIPPED", "CANCELLED"]
#: `ck_inventory_allocations_basis` 어휘 그대로다.
#:
#: ★ `FEFO_AUTO_SELECTED` 는 **시뮬레이션에서 규칙이 사람 자리에 선 것**이다
#:   (`fefo_allocation.allocate_reserved_stock_fefo`). 앞의 둘과 나눠 두는 이유는
#:   둘 다 *"사람이 무엇을 했나"* 를 적기 때문이다 —
#:
#:   ```text
#:   FEFO_TOOL_CONFIRMED  사람이 Tool 후보를 보고 **그대로 확정했다**
#:   HUMAN_OVERRIDE       사람이 후보와 **다르게 정했다**
#:   FEFO_AUTO_SELECTED   🔴 **사람이 없다.** FEFO 규칙이 그대로 골랐다
#:   ```
#:
#:   ⚠️ 자동 선택을 `FEFO_TOOL_CONFIRMED` 로 적으면 *"사람이 추천을 따랐다"* 가
#:      거짓으로 서고, `HUMAN_OVERRIDE` 로 적으면 없던 사람이 생긴다. 셋째 값이
#:      필요한 이유가 그것이고, 이 값은 **Master ↔ Logistics 합의 어휘**다.
AllocationBasis = Literal["FEFO_TOOL_CONFIRMED", "HUMAN_OVERRIDE", "FEFO_AUTO_SELECTED"]

#: 🔴 **사람이 직접 고를 수 있는 근거.** `FEFO_AUTO_SELECTED` 가 **빠져 있다.**
#:
#: ★ 셋 중 둘만 사람의 것이다. 자동 선택은 `fefo_allocation` 이 스스로 적는 값이고,
#:   사람이 그 값을 손으로 넣으면 **하지 않은 일을 장부에 적는 것**이 된다 —
#:   "규칙이 골랐다" 가 거짓으로 서고, 나중에 왜 그 Lot 이었는지 물을 때 답이 없다.
#:
#: ⚠️ **입력에만 쓴다. 조회에는 `AllocationBasis` 를 그대로 쓴다** — 자동으로 선
#:    할당도 사람이 보아야 하고, 여기서 좁히면 이미 적힌 사실을 못 읽게 된다.
#:
#:    ```text
#:    사람이 보내는 값 (Console Command)   HumanAllocationBasis   두 값
#:    장부에 적히는 값 · 조회 응답          AllocationBasis        세 값
#:    ```
HumanAllocationBasis = Literal["FEFO_TOOL_CONFIRMED", "HUMAN_OVERRIDE"]

RESERVATION_STATUSES: frozenset[str] = frozenset(get_args(ReservationStatus))
ALLOCATION_BASES: frozenset[str] = frozenset(get_args(AllocationBasis))


#: 🔴 기존 원장에 이미 있는 어휘다 (실측: OUT 75행). 새 사유를 만들지 않는다.
OUT_REASON_CODE = "SALE_FULFILLMENT"


class OutboundError(RuntimeError):
    """이 모듈이 내는 실패의 조상."""


class InvalidOutboundRequest(OutboundError, ValueError):
    """요청이 DB 계약이나 가용재고를 어긴다. **DML 전에 막는다.**"""


class ReservationConflict(OutboundError, ValueError):
    """같은 `reservation_id` 에 **다른 사실**의 예약이 이미 있다.

    🔴 수량을 조용히 덮어쓰지 않는다 — 덮으면 앞 요청이 소리 없이 사라진다.
    """


class OutboundIntegrityError(OutboundError, ValueError):
    """예약·할당·원장이 서로를 배반한다. **조용히 고치지 않는다.**"""


@dataclass(frozen=True)
class FefoCandidate:
    """FEFO 추천 한 줄. **추천일 뿐 고른 것이 아니다.**"""

    lot_id: str
    available_qty_kg: Decimal
    #: `repository` 와 **같은 식**으로 센다 — 새 유통기한 공식을 만들지 않는다.
    remaining_freshness_days: int | None
    received_at: date
    #: DB raw 등급 그대로. 정규화하지 않는다.
    grade: str | None


@dataclass(frozen=True)
class ReservationResult:
    applied: bool
    reservation_id: str
    status: ReservationStatus
    #: Sales 가 확정한 **원 요구량.** 물류가 이 값을 바꾸지 않는다.
    required_qty_kg: Decimal
    #: 물류가 **실제로 확보한 양** (= DB 행의 `reserved_qty_kg`). `reserve_stock` 은
    #: 둘이 늘 같고, `reserve_available_stock` 은 모자란 날 이 값만 작아진다.
    #:
    #: 🔴 **«지금 잡고 있는 양» 이 아니다.** 놓아준 예약(`RELEASED` · `CANCELLED`)도
    #:    이 값을 그대로 들고 있다 — 그 예약이 **확보했던 사실**은 놓아줬다고 사라지지
    #:    않기 때문이다 (WP-3 보정 2). 잡고 있나는 같은 결과의 `status` 가 답한다.
    #:
    #: 🔴 **기본값을 두지 않는다.** 두면 부분 확보를 부르는 쪽이 *"얼마나 잡혔나"* 를
    #:    묻지 않고도 통과하고, 그러면 못 잡은 몫이 조용히 사라진다.
    reserved_qty_kg: Decimal


@dataclass(frozen=True)
class AllocationRequest:
    """**사람이 고른** Lot 과 수량. 코드가 정하지 않는다."""

    lot_id: str
    quantity_kg: Decimal


@dataclass(frozen=True)
class AllocationResult:
    applied: bool
    allocation_ids: tuple[str, ...]
    reservation_status: ReservationStatus
    allocated_qty_kg: Decimal


@dataclass(frozen=True)
class ShipmentResult:
    applied: bool
    #: 이번에 실제로 내보낸 할당들.
    shipped_allocation_ids: tuple[str, ...]
    move_ids: tuple[str, ...]
    shipped_qty_kg: Decimal


@dataclass(frozen=True)
class AssignedAllocation:
    """이 예약이 한 Lot 에 이미 붙여 둔 할당 한 줄.

    ★ `allocation_id` 는 `(reservation_id, lot_id)` 에서 나오므로 Lot 당 **한 줄뿐**이다.
    """

    allocation_id: str
    lot_id: str
    allocated_qty_kg: Decimal
    status: AllocationStatus
    allocation_basis: AllocationBasis

    @property
    def is_auto_selected(self) -> bool:
        """🔴 **규칙이 정한 것인가.** 사람이 정한 할당은 규칙이 다시 안 만진다."""
        return self.allocation_basis == "FEFO_AUTO_SELECTED"

    @property
    def is_shipped(self) -> bool:
        """🔴 **이미 나갔나.** 나간 사실은 무슨 이유로도 다시 쓰지 않는다."""
        return self.status == "SHIPPED"


@dataclass(frozen=True)
class ReservationAllocationState:
    """한 예약의 **할당 진행 상태.** 읽기만 한 사실이고 아무것도 안 쓴다.

    ★ **자동 선택이 물어야 하는 것을 한 번에 답한다** — 어느 실행·어느 품목의 예약이며
      얼마를 확보했고 그중 얼마가 이미 Lot 에 붙었나.

    🔴 **`unassigned_qty_kg` 의 식이 `item_free_stock_qty` 의 SQL 과 같아야 한다.**

    ```text
    여기        max(reserved_qty_kg − Σ(ASSIGNED_ALLOCATION), 0)
    저기 (SQL)  GREATEST(r.reserved_qty_kg − Σ(a.status = ANY(assigned)), 0)
    ```

       ⚠️ 하나는 예약 하나를, 하나는 품목 전체를 세는 것뿐 **규칙은 한 줄이다.**
          갈리면 자동 할당이 잡을 수 있다고 본 몫을 `allocate_stock` 이 거절한다.
    """

    reservation_id: str
    sim_run_id: str
    item_id: str
    status: ReservationStatus
    #: Sales 가 확정한 원 요구량.
    required_qty_kg: Decimal
    #: 물류가 실제로 확보한 양.
    reserved_qty_kg: Decimal
    #: 이미 Lot 에 붙은 몫 (`ALLOCATED` · `PICKED` · `SHIPPED`).
    assigned_qty_kg: Decimal
    #: 이 예약이 이미 붙여 둔 할당들, `lot_id` 로 찾는다.
    #:
    #: ★ **수량만이 아니라 상태와 근거도 들고 있다** — 자동 선택이 *"이 Lot 을 더 쓸 수
    #:   있나"* 를 물으려면 셋이 다 필요하다. 나간 것(`SHIPPED`)인지, 사람이 정한
    #:   것(`HUMAN_OVERRIDE`)인지에 따라 답이 달라진다.
    assigned_by_lot: Mapping[str, AssignedAllocation]

    @property
    def unassigned_qty_kg(self) -> Decimal:
        """확보했는데 **아직 Lot 을 안 고른** 몫. 음수는 0 으로 본다.

        🔴 **놓아준 예약은 0 이다 (WP-3 보정 2).** `release_reservation` 이
           `reserved_qty_kg` 를 **보존**하게 되면서(과거 확보량을 지우지 않으려고)
           그 값이 놓아준 뒤에도 남는다. 여기서 그대로 빼면 *"이 예약이 아직 60kg
           붙일 게 남았다"* 가 되어 FEFO 가 놓아준 예약에 Lot 을 붙이러 간다.

        ```text
        확보한 양   reserved_qty_kg     보존된 과거 사실
        잡고 있나   status              ← 이 값이 답한다
        ```
        """
        if self.status not in HOLDING_RESERVATION:
            return Decimal(0)
        남은것 = self.reserved_qty_kg - self.assigned_qty_kg
        return 남은것 if 남은것 > 0 else Decimal(0)

    @property
    def assigned_lot_ids(self) -> frozenset[str]:
        """이 예약이 이미 붙여 둔 Lot 들."""
        return frozenset(self.assigned_by_lot)
