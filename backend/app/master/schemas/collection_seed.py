"""수금 사건 시드 결과 모델."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SeedStatus = Literal["SEEDED", "NOTHING_DUE", "UNREADABLE", "BLOCKED", "NOT_ATTEMPTED"]


@dataclass(frozen=True)
class CollectionSeedResult:
    """사건 생성 1회의 셈. 만든 것과 이미 있던 것을 가른다.

    :param created: 이번에 실제로 들어간 행 수.
    :param skipped: PK 충돌로 건너뛴 행 수 — 이미 있던 사건이다.
    """

    created: int
    skipped: int


@dataclass(frozen=True)
class CollectionSeedOutcome:
    """개장 응답에 실리는 결과. 못 한 것을 0 건으로 접지 않는다."""

    status: SeedStatus
    created: int = 0
    skipped: int = 0
    reason: str = ""
