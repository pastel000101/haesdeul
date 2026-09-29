"""재고 원장 쓰기의 어휘 · 결과 · 실패 종류.

★ 2026-09-30 재구성 BL-015: `logistics/ledger.py` 에서 옮겼다. 원장 쓰기 순서는 `service/ledger.py`,
  수량 · 멱등 판정은
  `domain/ledger.py`, SQL 은 `repository/ledger.py`, 잠금은 `repository/locks.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

#: **공개 진입점**(`record_inventory_move`)이 실행할 수 있는 Move Type.
#: DB CHECK 은 `DISPOSE` · `ADJUST` 도 받지만 여기서는 받지 않는다.
MoveType = Literal["IN", "OUT"]
SUPPORTED_MOVE_TYPES: frozenset[str] = frozenset({"IN", "OUT"})

#: 🔴 **`DISPOSE` 는 폐기 확정 전용 진입점(`_record_disposal_move`)으로만 들어온다.**
#:
#: ★ 아무 데서나 쓸 수 있게 열지 않는다 — 폐기는 되돌릴 수 없고 `ADJUST_IN` 도 없어서,
#:   *"업무 규칙을 통과한 경로"* 하나만 남겨 두는 것이 이 상수의 일이다.
#:   그 규칙(폐기대기 근거 · 예약/할당 보호 · 수량 한도)은 `disposal.py` 가 갖는다.
#:
#: ⚠️ `ADJUST` 는 여전히 어느 쪽으로도 안 들어온다 — 실사가 이번 범위 밖이다.
DISPOSE_MOVE_TYPES: frozenset[str] = frozenset({"DISPOSE"})

#: 같은 `move_id` 로 다시 들어왔을 때 *"같은 건인가"* 를 가르는 칸.
#: 🔴 `note` 도 넣는다 — 같은 id 로 다른 설명이 오면 그것도 다른 사실이다.
#:    빼면 "무결성 오류로 처리한다" 가 조용히 통과로 바뀐다.
IDENTITY_COLUMNS = (
    "sim_run_id",
    "lot_id",
    "sale_item_id",
    "move_type",
    "quantity_kg",
    "moved_at",
    "reason_code",
    "note",
)

#: Move Line 하나를 **사실로 식별**하는 칸. Header 와 같은 규율이다 — 같은 `move_id` 로
#: 다른 Pallet·Location·수량·설명이 오면 그것도 다른 사실이다.
#:
#: 🔴 `move_line_id` 는 여기 없다. 그것은 BIGSERIAL 이라 **재실행할 때마다 달라지는
#:    값**이고, 넣으면 정상 재시도가 영원히 Conflict 가 된다.
#: ★ `lot_id` 도 없다 — Header 의 것을 그대로 쓰므로 Header 대조에서 이미 갈린다.
LINE_IDENTITY_COLUMNS = (
    "pallet_id",
    "location_id",
    "quantity_kg",
    "note",
)


class InventoryLedgerError(RuntimeError):
    """원장 기록을 멈춘 이유. 아래 넷의 공통 조상이다."""


class LotNotFound(InventoryLedgerError, LookupError):
    """대상 Lot 이 없다. **만들지 않는다** — Lot 생성은 입고 단계 소유다."""


class UnsupportedMoveType(InventoryLedgerError, ValueError):
    """이번 판이 실행하지 않는 Move Type. `DISPOSE` · `ADJUST` 가 여기 걸린다."""


class InvalidMoveQuantity(InventoryLedgerError, ValueError):
    """수량이 양수가 아니다. DB CHECK(`quantity_kg > 0`)보다 먼저 막는다."""


class RemainingQuantityInsufficient(InventoryLedgerError, ValueError):
    """OUT 수량이 현재 잔량보다 크다. **Move 를 쓰기 전에** 멈춘다."""


class OriginalQuantityExceeded(InventoryLedgerError, ValueError):
    """IN 을 반영하면 `remaining_qty_kg > original_qty_kg` 가 된다.

    🔴 DB CHECK `inventory_lots_check` 를 우회하지 않는다 — 그 앞에서 막을 뿐이다.
       Lot 의 최초 수량을 늘리는 것은 원장이 할 일이 아니다 (입고 단계 소유).
    """


class MoveIdConflict(InventoryLedgerError, ValueError):
    """같은 `move_id` 가 **다른 사실**로 이미 있다. 무결성 위반이다.

    ★ 어느 쪽이 진짜인지 여기서 고르지 않는다 — 덮어쓰면 이전 사실이 에러 없이
      사라지고, 사라진 뒤에는 없었던 것과 구별되지 않는다.
    """


class MoveLineTotalMismatch(InventoryLedgerError, ValueError):
    """Move Line 합계가 Header 수량과 다르다.

    `v_move_line_integrity` 가 **사후에** 검출하는 상태를 애초에 못 만들게 막는다
    (그 뷰는 "비어 있어야 정상" 이다). Line 0 건은 정상이라 여기 걸리지 않는다 —
    Pallet 확정 전 입고가 그 상태다.
    """


@dataclass(frozen=True)
class MoveLine:
    """Move 한 건의 Pallet 단위 내역.

    ⚠️ **Pallet 도 Location 도 여기서 만들지 않는다.** 없는 id 를 주면 FK 가 막는다.
       Pallet 배치는 후속 단계 소유다.
    """

    quantity_kg: Decimal
    pallet_id: str | None = None
    location_id: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class LedgerResult:
    """한 번의 기록이 남긴 것.

    ★ `applied` 가 계약의 핵심이다 — `False` 는 **실패가 아니라 이미 반영됨**이다.
      호출자가 둘을 못 가리면 재시도가 잔량을 두 번 바꾸거나, 정상 재시도를
      오류로 읽는다.
    """

    move_id: str
    #: True = 이번 호출이 Move 를 넣고 잔량을 바꿨다 / False = 같은 Move 가 이미 있었다
    applied: bool
    #: 이 호출이 끝난 시점의 Lot 잔량 (반영했으면 반영 후, 아니면 현재값)
    remaining_qty_kg: Decimal
    #: 이번 호출이 넣은 Move Line 수. `applied=False` 면 항상 0 이다.
    line_count: int = 0
