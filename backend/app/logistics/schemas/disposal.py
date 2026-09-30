"""폐기 확정의 결과 · 실패 종류.

★ 2026-09-30 재구성 BL-015: `logistics/disposal.py` 에서 옮겼다. 순서는 `service/disposal.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


class DisposalError(RuntimeError):
    """이 모듈이 내는 실패의 조상."""


class InvalidDisposalRequest(DisposalError, ValueError):
    """요청이 계약이나 수량 한도를 어긴다. **DML 전에 막는다.**"""


class DisposalBlocked(DisposalError, ValueError):
    """폐기할 근거가 없다.

    🔴 **회전목표 초과는 근거가 아니다.** `disposal_candidate` 가 참이어야 하고,
       그 값의 유일한 출처는 Legacy 판매불가 기준이다.
    """


class DisposalIntegrityError(DisposalError, ValueError):
    """같은 폐기 참조에 **다른 사실**이 이미 있거나, 대상 Lot 이 없다."""


@dataclass(frozen=True)
class DisposalResult:
    """`confirm_disposal` 의 결과.

    🔴 `applied=False` 는 *"이 호출이 새 DISPOSE 를 남기지 않았다"* 다 — 같은 폐기가
       이미 적혀 있었다는 뜻이지 폐기가 없었다는 뜻이 아니다.
    """

    applied: bool
    move_id: str
    lot_id: str
    disposed_qty_kg: Decimal
    #: 이 호출이 끝난 시점의 Lot 잔량.
    remaining_qty_kg: Decimal
    #: 전량 폐기로 `DISPOSED` 까지 갔나.
    lot_status: str
