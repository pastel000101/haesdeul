"""개장 관문 결과 모델."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class FailedPart(BaseModel):
    """그날 열리지 않은 파트 하나.

    관문이 막힌 경우(`NEVER_OPENED` · `NOT_OPENED` · `REJECTED_GAP`)에 채워진다. 개장
    여부 자체를 읽지 못해 막힌 경우에는 비어 있다.
    """

    part: str
    reason: str = ""


class DayGate(BaseModel):
    """개장 관문 응답. 계약에 정한 여덟 칸을 그대로 싣는다.

    화면은 `gate` 만 보고 진행 여부를 정한다. `result` 는 판정 갈래이고, `next_action` 은
    막혔을 때의 다음 걸음이다(`PASS` 면 null).
    """

    as_of: date
    #: 화면은 이것만 보고 막는다. `result` 를 해석하게 두지 않는다.
    gate: Literal["PASS", "BLOCKED"]
    result: Literal["OPENED", "ALREADY_OPENED", "NOT_OPENED", "REJECTED_GAP", "NEVER_OPENED"]
    #: 마지막으로 열린 날. 못 찾으면 `None`.
    last_opened_date: date | None = None
    #: `last_opened_date` 와 `as_of` 의 달력일 차이. 못 찾으면 `None`.
    gap_days: int | None = None
    failed_parts: list[FailedPart] = Field(default_factory=list)
    reason: str = ""
    #: 키가 항상 있고 `PASS` 면 `None` 이다. 칸을 없애면 화면이
    #: `'next_action' in resp` 를 먼저 물어야 하고, 그게 판매가 피하자고 한 모양이다.
    next_action: (
        Literal[
            "OPEN_DAY_REQUIRED",
            "RETRY_OPEN_DAY",
            "ADMIN_FORCE_OPEN_REQUIRED",
            "SPLIT_FORCE_OPEN_REQUIRED",
            "CONTACT_OPERATOR",
        ]
        | None
    ) = None
