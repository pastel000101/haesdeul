"""재무 일마감 결과 — 마스터 마감 등록소가 마스터 모델을 모른 채 받는 모양.

★ 2026-09-29 재구성 BL-014: `finance/closing.py` 에서 옮겼다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class FinanceDayClosingResult:
    """Structural result consumed by Master's closing port without importing it."""

    part: str
    status: Literal["CLOSED", "NOTHING_DUE", "BLOCKED"]
    reason: str = ""
    closed: list[str] | None = None
    created: int = 0

    def __post_init__(self) -> None:
        if self.closed is None:
            object.__setattr__(self, "closed", [])
