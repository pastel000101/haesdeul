"""물류 에이전트 실행이력 한 줄 (`logistics_agent_runs`).

저장은 `service/run_history.py`, 조회는 `readmodel/runs.py`, SQL 은 `repository/runs.py` 에 있다.
"""

from datetime import date, datetime
from typing import TypedDict
from uuid import UUID

from app.logistics.schemas.agent import FinalVerdict, LogisticsCycle, RuntimeStatus


class LogisticsAgentRun(TypedDict):
    run_id: UUID
    cycle: LogisticsCycle
    as_of: date
    snapshot_id: str | None
    runtime_status: RuntimeStatus
    verdict: FinalVerdict | None
    request_payload: dict[str, object]
    response_payload: dict[str, object]
    created_at: datetime
