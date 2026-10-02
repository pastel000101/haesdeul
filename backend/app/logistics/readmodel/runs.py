"""물류 에이전트 실행이력 조회 — 공개 함수 하나가 조회 연결 하나를 빌린다.

SQL 은 `repository/runs.py`.
"""

from datetime import date
from typing import cast
from uuid import UUID

from app.core import db as core_db
from app.logistics.repository.runs import select_logistics_agent_run, select_logistics_agent_runs
from app.logistics.schemas.agent import FinalVerdict, LogisticsCycle, RuntimeStatus
from app.logistics.schemas.runs import LogisticsAgentRun


def get_logistics_agent_run(run_id: UUID) -> LogisticsAgentRun:
    """run_id로 Logistics Agent 실행이력 한 건을 조회한다. 조회 연결 하나를 빌린다."""
    with core_db.read_connection() as conn:
        row = select_logistics_agent_run(conn, run_id=run_id)
    if row is None:
        raise LookupError(f"Logistics Agent run was not found: {run_id}")
    return cast(LogisticsAgentRun, row)


def list_logistics_agent_runs(
    *,
    cycle: LogisticsCycle | None = None,
    as_of: date | None = None,
    runtime_status: RuntimeStatus | None = None,
    verdict: FinalVerdict | None = None,
    limit: int = 100,
) -> list[LogisticsAgentRun]:
    """선택한 필터로 최신 Logistics Agent 실행이력을 조회한다. 조회 연결 하나를 빌린다."""
    with core_db.read_connection() as conn:
        return cast(
            list[LogisticsAgentRun],
            select_logistics_agent_runs(
                conn,
                cycle=cycle,
                as_of=as_of,
                runtime_status=runtime_status,
                verdict=verdict,
                limit=limit,
            ),
        )
