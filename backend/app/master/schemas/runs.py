"""마스터 실행 이력 행 모델.

★ 2026-09-30 재구성 BL-018: `master/run_repository.py` 에서 옮겼다 — `RunCycle`, `MasterAgentRun`,
  `DayRunCount`.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TypedDict
from uuid import UUID

#: 마스터가 도는 사이클. **`A` · `B` 는 오케 어휘라 없다.**
#:
#: ★ `STATUS` 가 새로 들어왔다. 옛 표에는 없어서 조회를 이력에 안 적고 있었고,
#:   그래서 예산을 쓰는 호출이 이력에서 보이지 않았다. M-16 이 막으려는 것이
#:   정확히 "안 보이는 호출" 이다.
RunCycle = str  # PROCUREMENT | SALES | STATUS | DAY - CHECK 는 DB 가 강제한다


class MasterAgentRun(TypedDict):
    run_id: UUID
    request_id: str | None
    as_of: date
    cycle: str
    run_seq: int
    item: str | None
    end_code: str | None
    runtime_status: str
    coverage_ran: int | None
    coverage_total: int | None
    elapsed_ms: int | None
    plan: list[dict[str, object]] | None
    request_payload: dict[str, object]
    response_payload: dict[str, object]
    sim_run_id: str | None
    created_at: datetime


class DayRunCount(TypedDict):
    """**행이 있는 날** 하나의 집계. 행이 없는 날은 여기 없다.

    🔴 **표는 없는 것을 말할 수 없다.** 안 돈 날은 이 목록에서 그냥 빠져 있고, 그
       빈 자리가 *"안 도는 날이라 없다"* 인지 *"실행일인데 없다"* 인지는 여기서
       답하지 않는다 — 범위를 아는 `app/master/report/walk_report.py` 가 답한다.

    ★ **행 수와 뜻을 따로 낸다.** `runs` 는 몇 행인가이고 `end_codes` 는 그 행들이
      어떻게 끝났나다. 둘을 섞으면 *"행이 4건이니 개장일이다"* 같은 오독이 나온다 —
      그 4행이 전부 *"실행일이 아니다"* 를 사유로 달고 있어도 행 수는 4다.
    """

    as_of: date
    #: 그날 행 수.
    runs: int
    #: 그날 나온 종료코드와 건수. `end_code` 가 NULL 인 행은 세지 않는다.
    end_codes: dict[str, int]
    #: 그날 나온 품목. 품목 칸이 빈 행(관문·조회)은 빼고 모은다.
    items: tuple[str, ...]
    #: 장부 관문 행(`#465`)이 있었나 — 품목이 없고 `E4_NOT_STARTED` 인 행.
    gate_blocked: bool
