"""승인 취소 등록소 — 파트별 취소 구현의 Protocol · 등록 · 조회.

취소를 실제로 부르는 것은 `service/cancellation.py` 다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Protocol

from app.contracts.commitment import ApprovedCommitment
from app.master.registry.transition import PARTS, TransitionPart


class ApprovalCancellation(Protocol):
    """승인분을 자기 장부에서 물리는 방식. 각 부서가 소유한다.

    `build` 를 순수하게 나누지 않는다. `apply_approval` 과 다른 점이다 — "무엇을 물릴 수
    있나" 는 적힌 사실이고 부서가 DB 를 읽어야 안다(재무는 `payables.status` 를 잠그고
    읽는다). `DayOpening` 이 `conn` 을 받는 것과 같은 이유다.

    `conn` 은 받기만 한다. commit·rollback·close 를 하지 않는다 — 트랜잭션 경계는
    마스터가 쥔다.

    `financing_mode` 는 마스터가 싣는다(재무 요청). `finance_states` 정본이
    `(sim_run_id, financing_mode, state_date)` 라 `sim_run_id + state_date` 만으로 조회하면
    같은 날짜에 mode 가 둘일 때 ambiguous 로 막힌다(실측: `2025-12-31` 하루가 그렇다).
    재무는 mode 를 고르지 않는다. 고르는 순간 그 선택이 조용히 굳고, 나중에 누구도 왜 그
    축이었는지 못 찾는다 — `purchase_ids` 를 재무가 지어내면 안 되는 것과 같은 자리다.

    물류도 같은 인자를 받는다. 쓰지 않더라도 두 파트가 같은 모양이어야 호출부가 하나로
    선다. 인자를 한쪽에만 두면 `purchase_ids` 를 재무에만 줬을 때 물류 Arrival 이 막혔던
    것과 같은 일이 생긴다.

    :param commitment: 무엇을 승인했었나. `as_of` 는 승인일이다.
    :param cancelled_on: 취소 사건일. `commitment.as_of` 와 다를 수 있다.
    :param target_state_date: `cancelled_on + 1일`. 부서가 다시 계산하지 않는다.
    :param purchase_ids: 회차(seq) → purchase_id. 승인 때와 같은 매핑이다.
    :param financing_mode: 이 실행의 재무 축. 마스터가 실어 준다 — 부서가 임의로
        고르거나 최신 상태를 추론하지 않는다.
    """

    def cancel(
        self,
        conn: Any,
        *,
        commitment: ApprovedCommitment,
        cancelled_on: date,
        target_state_date: date,
        purchase_ids: Mapping[int, str],
        financing_mode: str,
    ) -> None: ...


# ── 등록소 ──────────────────────────────────────────────────────────────
#
# 전이 등록소와 따로 둔다. 같은 사전에 넣으면 "전이는 되는데 취소는 안 되는" 상태를
# 표현할 수 없다.

_CANCELLATIONS: dict[TransitionPart, Any] = {}


def register_cancellation(part: TransitionPart, impl: Any) -> None:
    """취소 구현을 등록한다. `registry/bootstrap.py` 의 `wire_registries` 가 부른다."""
    if part not in PARTS:
        raise ValueError(f"취소 파트가 아니다: {part!r}. 가능: {', '.join(PARTS)}")
    _CANCELLATIONS[part] = impl


def registered_cancellations() -> Mapping[TransitionPart, Any]:
    """지금 등록된 취소 구현. 읽기용 사본이다."""
    return dict(_CANCELLATIONS)


def cancellation_missing() -> tuple[str, ...]:
    """취소 구현이 등록되지 않은 파트. `PARTS` 순서를 지킨다."""
    return tuple(part for part in PARTS if part not in _CANCELLATIONS)


def reset() -> None:
    """등록을 비운다. 검사용이다."""
    _CANCELLATIONS.clear()
