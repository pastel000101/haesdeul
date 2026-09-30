"""승인 취소 등록소 — 파트별 취소 구현의 Protocol · 등록 · 조회.

★ 2026-09-30 재구성 BL-018: `master/cancellation.py` 에서 옮겼다 — `ApprovalCancellation`,
  `_CANCELLATIONS`, `register_cancellation`, `registered_cancellations`, `cancellation_missing`,
  `reset`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Protocol

from app.contracts.commitment import ApprovedCommitment
from app.master.registry.transition import PARTS, TransitionPart


class ApprovalCancellation(Protocol):
    """승인분을 자기 장부에서 물리는 방식. **각 부서가 소유한다.**

    ★ **`build` 를 순수하게 나누지 않는다.** `apply_approval` 과 다른 점이다 —
      *"무엇을 물릴 수 있나"* 는 **적힌 사실**이고 부서가 DB 를 읽어야 안다
      (재무는 `payables.status` 를 잠그고 읽는다). `DayOpening` 이 `conn` 을 받는
      것과 같은 이유다.

    ★ **`conn` 은 받기만 한다.** commit·rollback·close 를 하지 않는다 — 트랜잭션
      경계는 마스터가 쥔다.

    🔴 **`financing_mode` 를 마스터가 싣는다** (재무 요청 2026-09-06).

      `finance_states` 정본이 `(sim_run_id, financing_mode, state_date)` 인데 취소
      조회는 `sim_run_id + state_date` 만 보고 있었고, **같은 날짜에 mode 가 둘이면
      ambiguous 로 막힙니다.** 실측으로 `2025-12-31` 하루가 이미 그렇다.

      ★ **재무가 고르지 않겠다고 했고 그것이 맞다.** 고르는 순간 그 선택이 조용히
        굳고, 나중에 누구도 왜 그 축이었는지 못 찾는다 — `purchase_ids` 를 재무가
        지어내면 안 되는 것과 같은 자리다.

      ⚠️ **물류도 같은 인자를 받는다. 안 쓰더라도.** 두 파트가 같은 모양이어야
        호출부가 하나로 서고, `purchase_ids` 를 반쪽으로 뒀다가 물류 Arrival 이
        막힌 자리가 그 교훈이다.

    :param commitment: 무엇을 승인했었나. `as_of` 는 **승인일**이다.
    :param cancelled_on: **취소 사건일.** `commitment.as_of` 와 다를 수 있다.
    :param target_state_date: `cancelled_on + 1일`. 부서가 다시 계산하지 않는다.
    :param purchase_ids: 회차(seq) → purchase_id. 승인 때와 **같은 매핑**이다.
    :param financing_mode: 이 실행의 재무 축. **마스터가 실어 준다** (재무 요청
        2026-09-06) — 부서가 임의로 고르거나 최신 상태를 추론하지 않는다.
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
# ★ **전이 등록소와 따로 둔다.** 같은 사전에 넣으면 *"전이는 되는데 취소는 안 되는"*
#   상태를 표현할 수 없다. 실제로 지금이 그 상태다 — 전이는 둘 다 섰고 취소는 재무만
#   섰다.

_CANCELLATIONS: dict[TransitionPart, Any] = {}


def register_cancellation(part: TransitionPart, impl: Any) -> None:
    """취소 구현을 등록한다. 재무·물류 모듈이 임포트 시점에 부른다."""
    if part not in PARTS:
        raise ValueError(f"취소 파트가 아니다: {part!r}. 가능: {', '.join(PARTS)}")
    _CANCELLATIONS[part] = impl


def registered_cancellations() -> Mapping[TransitionPart, Any]:
    """지금 등록된 취소 구현. **읽기용 사본**이다."""
    return dict(_CANCELLATIONS)


def cancellation_missing() -> tuple[str, ...]:
    """아직 취소 구현이 없는 파트. **`PARTS` 순서를 지킨다.**"""
    return tuple(part for part in PARTS if part not in _CANCELLATIONS)


def reset() -> None:
    """등록을 비운다. 검사용이다."""
    _CANCELLATIONS.clear()
