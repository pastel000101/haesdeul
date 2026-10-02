"""걷기 성적표 응답 모델."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class WalkDay(BaseModel):
    """걷기의 하루. 행 수와 그 뜻을 따로 낸다.

    주의: 행 수를 뜻으로 읽지 않는다. 그날 행이 여러 건이어도 전부 "실행일이 아니다" 를
    사유로 달고 있을 수 있다. 행이 있다고 실행일이라는 뜻이 아니므로 `runs` ·
    `is_execution_day` · `end_codes` 를 서로 다른 칸에 둔다.
    """

    as_of: date
    #: §2 의 판정 하나. `end_code` 가 아니다 — 축이 다르다.
    verdict: str
    #: 이 날 판단을 도는 날인가. `None` 은 모른다 (`UNKNOWN_CALENDAR`).
    is_execution_day: bool | None
    #: 그날 행 수. 0 이면 표에 그날 행이 없었다는 뜻이다.
    runs: int = 0
    #: 그날 나온 종료코드와 건수.
    end_codes: dict[str, int] = {}
    #: 그날 나온 품목.
    items: list[str] = []
    #: 장부 관문 행(`#465`)이 있었나.
    gate_blocked: bool = False


class WalkReport(BaseModel):
    """한 걷기의 성적표. 물어본 범위(`start` ~ `end`) 안의 날만 담는다.

    `summary` 는 판정별 날 수이고 행 수가 아니다. 둘을 한 칸에 담으면 하루에 여러 품목을
    돈 날이 여러 날처럼 읽힌다.

    주의: `summary` 의 `NO_ROW_ON_EXECUTION_DAY` 를 그대로 "공백 N일" 로 읽지 않는다.
    물어본 범위가 그 걷기가 실제로 걸은 범위보다 넓으면 걷지 않은 날도 모두 여기에
    들어간다. 이 성적표는 물어본 범위에서 이 걷기가 무엇을 남겼는지에 답하며, 걷기가
    얼마나 실패했는지를 재지 않는다.
    """

    sim_run_id: str
    start: date
    end: date
    #: 공휴일을 봤나. 거짓이면 주말만 갈랐고, 설·추석이
    #: `NO_ROW_ON_EXECUTION_DAY` 로 나온다 — 그건 없는 공백이다.
    holiday_calendar_used: bool
    days: list[WalkDay]
    #: 판정별 날 수. 안 나온 판정은 칸이 없다.
    summary: dict[str, int]
