"""하루 넘김 판정 — 그날 활성 실행 목록으로 «내 실행의 날이 열렸나».

★ 2026-09-30 재구성 BL-015: `logistics/day_open.py` 에서 옮겼다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.logistics.schemas.day_open import LogisticsRunAmbiguous
from app.logistics.schemas.vocabulary import USAGE_SCOPE


def day_open_from_runs(실행들: list[Any], *, pinned: bool, as_of: date) -> bool:
    """그날 활성 실행 목록 → 내 실행의 날이 열렸나. 못 좁혔는데 둘 이상이면 멈춘다.

    DB 를 만지지 않는다.
    """
    if pinned:
        # ★ 조회가 이미 내 실행으로 좁혀져 있다 — 나온 것이 있으면 내 행이다.
        return bool(실행들)
    if len(실행들) > 1:
        raise LogisticsRunAmbiguous(
            f"같은 날에 활성 실행이 둘 이상이다 (as_of={as_of},"
            f" usage_scope={USAGE_SCOPE}, 실행 {len(실행들)}개 이상)."
            " 어느 실행의 하루 넘김인지 모르는 채로 열렸다고 답하지 않는다 —"
            " LogisticsDayOpening(sim_run_id=...) 로 주입해야 한다."
        )
    return bool(실행들)
