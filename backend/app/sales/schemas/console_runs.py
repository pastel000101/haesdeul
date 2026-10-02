"""판매 운영 콘솔의 실행이력 응답 — `readmodel/console_runs.py` 가 채운다."""

from datetime import date, datetime

from pydantic import BaseModel


class ConsoleSalesRun(BaseModel):
    run_id: str
    #: Null when the stored context carried no business key.
    request_id: str | None
    sim_run_id: str
    as_of: date
    partner_id: str | None
    partner_name: str | None
    item: str | None
    runtime_status: str
    #: Sales itself stores no verdict; Finance owns that word.  Null, not invented.
    verdict: str | None
    llm_status: str | None
    #: Master's end code for the same request, read from Master's own table.
    master_end_code: str | None
    created_at: datetime


class ConsoleSalesRunsResponse(BaseModel):
    sim_run_id: str
    rows: list[ConsoleSalesRun]
