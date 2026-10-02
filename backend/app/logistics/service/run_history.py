"""물류 에이전트 실행이력 저장 — 한 저장 = 풀 연결 하나 · 트랜잭션 하나.

SQL 은 `repository/runs.py` 다.
"""

from datetime import date
from typing import cast

from app.core import db as core_db
from app.logistics.repository.runs import insert_logistics_agent_run
from app.logistics.schemas.agent import FinalVerdict, LogisticsCycle, RuntimeStatus
from app.logistics.schemas.runs import LogisticsAgentRun


def save_logistics_agent_run(
    *,
    cycle: LogisticsCycle,
    as_of: date,
    snapshot_id: str | None,
    runtime_status: RuntimeStatus,
    verdict: FinalVerdict | None,
    request_payload: dict[str, object],
    response_payload: dict[str, object],
) -> LogisticsAgentRun:
    """완성된 Logistics Agent Request와 Response를 실행이력으로 저장한다.

    한 저장 = 풀 연결 하나 · 트랜잭션 하나 — 정상이면 commit, 예외면 rollback 하고
    저장한 행을 돌려준다.
    """
    if response_payload.get("verdict") != verdict:
        raise ValueError("Logistics run verdict metadata must match response_payload.verdict")
    with core_db.connection() as conn, core_db.transaction(conn):
        row = insert_logistics_agent_run(
            conn,
            cycle=cycle,
            as_of=as_of,
            snapshot_id=snapshot_id,
            runtime_status=runtime_status,
            verdict=verdict,
            request_payload=request_payload,
            response_payload=response_payload,
        )
    return cast(LogisticsAgentRun, row)
