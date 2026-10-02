"""Lot 회전 · 신선도 파생값과 품목 정책 한 벌.

계산은 `domain/turnover.py`, SQL 은 `repository/turnover.py`, 읽기 조합은
`readmodel/turnover.py` 에 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

#: 회전 상태. Persona 05 §4 어휘 그대로다.
#:
#: ```text
#: NORMAL                   판매우선 없음
#: SELL_PRIORITY            판매를 우선 검토해야 하는 물류 Signal
#: STORAGE_TARGET_EXCEEDED  회사 내부 회전목표 초과 (판매불가 아님)
#: ```
TurnoverStatus = Literal["NORMAL", "SELL_PRIORITY", "STORAGE_TARGET_EXCEEDED"]


@dataclass(frozen=True)
class LotTurnover:
    """Lot 하나의 회전·신선도 파생값. 아무것도 바꾸지 않는 계산 결과다."""

    lot_id: str
    item_id: str
    received_at: date
    remaining_qty_kg: Decimal
    #: `as_of − received_at`. 미래 입고일이면 음수다 — 0 으로 보정하지 않는다.
    elapsed_days: int
    #: 회전 정책이 없는 품목이면 `None`. 0 으로 채우지 않는다.
    remaining_turnover_days: int | None
    turnover_status: TurnoverStatus | None
    #: `turnover_status` 가 `None` 이면 거짓이다 — 모르는 것을 신호로 올리지 않는다.
    sell_priority: bool
    #: Legacy 신선도 축. 정책이 없으면 `None`.
    remaining_freshness_days: int | None
    #: `remaining_freshness_days` 계산에 실제 쓴 유효 보관한계 (등급 계수 반영).
    #:
    #: 잔여와 함께 낸다 — `domain/snapshot.inventory_lot_from_row` 가 스냅샷에
    #: 같은 칸을 싣는 이유와 같다: 신선도 잔여 비율의 분모는 원값이 아니라 이
    #: 값이어야 하고, 하나만 주면 받는 쪽이 남은 하나로 역산한다.
    #:   둘은 함께 없거나 함께 있다.
    effective_freshness_limit_days: int | None
    #: 회전 정책의 판매우선 경계 원값. 정책이 없으면 `None`.
    #:
    #: `turnover_status` 로 접지 않는다 — 상태는 "판매우선인가" 이고 이 값은
    #: "며칠 남았을 때부터 그렇게 보나" 다. 파생 상태만 내면 그 경계를 쓰는
    #: 소비자가 정책 표를 다시 읽는다(같은 값의 두 번째 조회).
    sell_priority_remaining_days: int | None
    #: 회전목표와 무관하다. 근거는 Legacy 판매불가 기준 하나뿐이다.
    disposal_candidate: bool


@dataclass(frozen=True)
class ItemPolicy:
    """품목 하나의 보관·회전 정책 한 벌. 두 표를 한 번에 읽는다.

    `load_lot_turnover` 와 같은 두 표를 본다. 다른 점은 축이다 — 저쪽은 Lot 이라
    재고가 있는 품목만 나오고, 이쪽은 품목이라 재고 0kg 인 품목의 정책도 읽힌다
    (`repository/current.get_item_storage_policies` 가 Lot 에서 역산하지 않는 것과 같은 이유).

    관측일이 없다. 두 표에 유효일 칸이 없어 "그날 그 정책이었나" 를 알 수 없다
    (`schemas/monitoring.POLICY_OBSERVED_AS_OF`). 그래서 `as_of` 를 받지 않는다 —
    받으면 과거를 복원한 척이 된다.
    """

    item_id: str
    item_name: str | None
    #: 보관 (`item_storage_policies`). 정책이 없으면 `None` — 0 으로 채우지 않는다.
    operational_limit_days: int | None
    medium_grade_factor: Decimal | None
    #: 회전 (`item_turnover_policies`). 실측 5 중 3 품목뿐이다.
    operational_turnover_target_days: int | None
    sell_priority_remaining_days: int | None

    @property
    def has_storage_policy(self) -> bool:
        return self.operational_limit_days is not None

    @property
    def has_turnover_policy(self) -> bool:
        return self.operational_turnover_target_days is not None
