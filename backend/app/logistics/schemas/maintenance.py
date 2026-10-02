"""자동 유지보수(폐기대기 전량 폐기 · 빈 Pallet 정리)의 결과 · 실패 종류.

순서는 `service/maintenance.py` 에 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

#: Lot 하나가 이번 사이클에서 어떻게 됐나. DB 어휘가 아니다 — 파이썬 결과값이고
#: 어느 표에도 안 적힌다. 상태 표를 새로 만들지 않으려고 여기 둔다.
MaintenanceOutcome = Literal[
    #: 전량 폐기했다. 잔량 0 · Lot status DISPOSED.
    "DISPOSED",
    #: 살아있는 할당이 있어 손대지 않았다. 자동 부분 폐기를 하지 않는다.
    "SKIPPED_HELD_ALLOCATION",
    #: 이미 잔량 0 이라 폐기할 것이 없고, 남은 Pallet 자리만 돌려줬다.
    "PALLETS_EMPTIED",
    #: 도메인이 거절했고 이번 사이클이 이 Lot 에서 아무것도 못 했다. 사람이 봐야 한다.
    "FAILED",
]


class InvalidAutoMaintenanceRequest(ValueError):
    """요청 자체가 성립하지 않는다. DB 에 묻기 전에 막는다.

    `reason_code` · `recorded_by` · `occurred_at` 을 물류가 지어내지 않는다.
    첫째는 `confirm_disposal` 이 "호출자가 준다" 로 못박은 칸이고
    (`inventory_moves.reason_code` 에 CHECK 이 없다), 둘째는
    `pallet_events.recorded_by` 가 NOT NULL 인데 자동화 주체를 코드가 지어내면
    그 이름이 장부에 사실로 남는다. 셋째는 시뮬레이션 시간축의 주인이 호출자라서다
    (`fefo_allocation.decided_at` 과 같은 규율).
    """


@dataclass(frozen=True)
class LotMaintenanceOutcome:
    """Lot 하나의 결과. 터진 것도 값으로 남는다."""

    lot_id: str
    outcome: MaintenanceOutcome
    #: 이번 사이클이 실제로 없앤 양. 안 없앴으면 0.
    disposed_qty_kg: Decimal
    #: 이 Lot 을 처리한 뒤의 잔량.
    remaining_qty_kg: Decimal
    #: 이번 사이클이 실제로 비운 Pallet. 순서를 지킨다.
    emptied_pallet_ids: tuple[str, ...] = ()
    #: 자리 반환이 도중에 막혔으면 그 사유. 안 막혔으면 `None`.
    #:
    #: `outcome` 과 따로 둔다. 폐기는 됐는데 자리만 못 돌려준 날이 있고, 그때
    #: Lot 전체를 `FAILED` 로 적으면 없어진 재고가 결과에서 사라진다. 두 사실을
    #: 한 칸에 접지 않는다 — `outcome` 은 재고를, 이 칸은 자리를 말한다.
    pallet_cleanup_error: str | None = None
    #: 왜 건너뛰었나 · 무엇이 터졌나. 사람이 읽는 줄이다.
    reason: str = ""


@dataclass(frozen=True)
class AutoMaintenanceResult:
    """자동 유지보수 1회의 결과.

    본 것과 한 것을 함께 싣는다. `examined_lots` 가 없으면 "0건 처리" 가
    "아무것도 안 봤다" 인지 "볼 것이 없었다" 인지 구별되지 않는다.
    """

    as_of: date
    sim_run_id: str
    #: 회전 조회가 훑은 Lot 수 (잔량 > 0 인 것). 후보가 아닌 Lot 은 `lots` 에 안 실린다.
    examined_lots: int
    #: 손댔거나 일부러 건너뛴 Lot 만. 정상 Lot 은 여기 없다.
    lots: tuple[LotMaintenanceOutcome, ...]

    @property
    def disposed_lot_ids(self) -> tuple[str, ...]:
        return tuple(one.lot_id for one in self.lots if one.outcome == "DISPOSED")

    @property
    def disposed_qty_kg(self) -> Decimal:
        return sum((one.disposed_qty_kg for one in self.lots), start=Decimal(0))

    @property
    def emptied_pallet_ids(self) -> tuple[str, ...]:
        return tuple(pid for one in self.lots for pid in one.emptied_pallet_ids)

    @property
    def skipped(self) -> tuple[LotMaintenanceOutcome, ...]:
        return tuple(one for one in self.lots if one.outcome == "SKIPPED_HELD_ALLOCATION")

    @property
    def failures(self) -> tuple[LotMaintenanceOutcome, ...]:
        """사람이 봐야 하는 줄. `FAILED` 만 보지 않는다.

        폐기는 됐는데 자리 반환이 막힌 Lot 은 `outcome` 이 `DISPOSED` 인 채로
        남는다 (폐기 사실을 숨기지 않으려고). 그 줄을 여기서 빠뜨리면 아무도
        안 보는 실패가 된다.
        """
        return tuple(
            one
            for one in self.lots
            if one.outcome == "FAILED" or one.pallet_cleanup_error is not None
        )

    @property
    def pallet_cleanup_failures(self) -> tuple[LotMaintenanceOutcome, ...]:
        """자리 반환만 막힌 줄. 재고 쪽은 성공했을 수 있다."""
        return tuple(one for one in self.lots if one.pallet_cleanup_error is not None)
