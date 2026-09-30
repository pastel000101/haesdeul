"""운영 콘솔 재무 실행이력 응답.

★ 2026-09-29 재구성 BL-014: `finance/console_runs.py` 에서 응답 모델만 옮겼다(필드 그대로).
"""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


class ConsoleFinanceRun(BaseModel):
    run_id: str
    request_id: str
    sim_run_id: str
    as_of: date
    mode: str
    runtime_status: str
    business_status: str
    #: `PASS` / `REVIEW_REQUIRED` / `FAIL`, or null when the stored reply carried no
    #: verdict.  Null is "this run did not conclude", never a verdict of its own.
    verdict: str | None
    llm_status: str
    #: The deterministic block the agent actually stored.  Not recomputed here.
    deterministic_result: dict[str, Any] | None
    evidence: list[str] | None
    #: Finance stores no free-text interpretation per run yet; the column is carried
    #: as null rather than filled with the reasoning of some other layer.
    interpretation: str | None
    created_at: datetime


class ConsoleFinanceRunsResponse(BaseModel):
    sim_run_id: str
    rows: list[ConsoleFinanceRun]
