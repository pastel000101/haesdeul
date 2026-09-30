"""걷기 성적표 응답 모델.

★ 2026-09-30 재구성 BL-018: `master/walk_report.py` 에서 옮겼다 — `WalkDay`, `WalkReport`.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class WalkDay(BaseModel):
    """걷기의 하루. **행 수와 뜻을 따로 낸다.**

    ⚠️ **행 수를 뜻으로 읽지 마라.** `01-31` 에 행이 4건이었지만 그 4행은 전부
      *"실행일이 아니다"* 를 사유로 달고 있었다. 행이 있다는 것은 개장일이라는
      뜻이 아니다 — 그래서 `runs` 와 `is_execution_day` 와 `end_codes` 가 서로
      다른 칸에 있다.
    """

    as_of: date
    #: §2 의 판정 하나. `end_code` 가 아니다 — 축이 다르다.
    verdict: str
    #: 이 날 판단을 도는 날인가. **`None` 은 모른다** (`UNKNOWN_CALENDAR`).
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
    """한 걷기의 성적표. **물어본 범위 밖은 여기 없다.**

    ★ `summary` 는 **날 수**다 — 행 수가 아니다. 둘을 한 칸에 담으면 하루에 여러
      품목을 돈 날이 여러 날처럼 읽힌다.

    🔴 **`summary` 의 `NO_ROW_ON_EXECUTION_DAY` 를 그대로 「공백 N일」로 읽지 마라.**
      부르는 쪽이 준 범위가 그 걷기가 실제로 걸은 범위보다 넓으면, 걷지도 않은 날이
      전부 거기 들어간다 (`NO_ROW_ON_EXECUTION_DAY` 주석의 실측). 이 성적표가
      답하는 것은 *"물어본 범위에서 이 걷기가 무엇을 남겼나"* 이지
      *"이 걷기가 얼마나 실패했나"* 가 아니다.
    """

    sim_run_id: str
    start: date
    end: date
    #: 🔴 **공휴일을 봤나.** 거짓이면 주말만 갈랐고, 설·추석이
    #: `NO_ROW_ON_EXECUTION_DAY` 로 나온다 — 그건 없는 공백이다.
    holiday_calendar_used: bool
    days: list[WalkDay]
    #: 판정별 **날 수**. 안 나온 판정은 칸이 없다.
    summary: dict[str, int]
