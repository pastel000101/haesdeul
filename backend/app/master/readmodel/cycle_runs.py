"""Critic 실행 이력 조회 — 조회 연결을 빌려 `orchestrator_agent_runs` 를 읽는다.

공개 함수 하나가 조회 연결(autocommit) 하나를 빌린다. SQL 은 `repository/cycle_runs.py`.
"""

from __future__ import annotations

from datetime import date
from typing import cast
from uuid import UUID

from app.core import db as core_db
from app.core.settings import get_db_schema
from app.master.repository.cycle_runs import (
    select_cycle_run,
    select_cycle_runs,
    select_latest_cycle_run,
)
from app.master.schemas.cycle import Agent, OrchestratorAgentRun


def get_run(run_id: UUID) -> OrchestratorAgentRun:
    schema = get_db_schema()
    with core_db.read_connection() as conn:
        row = select_cycle_run(conn, run_id, schema=schema)
    if row is None:
        raise LookupError(f"실행이력을 찾을 수 없습니다: {run_id}")
    return cast(OrchestratorAgentRun, row)


def get_run_by_request_id(request_id: str) -> OrchestratorAgentRun:
    """마스터 업무 키로 찾는다 — 최신 1건.

    같은 `request_id` 로 두 번 돌면(재실행) 행이 둘이 된다. 최신을 돌려준다 —
    사용자가 "그 요청 어떻게 됐냐"고 물으면 마지막 결과를 기대한다.
    """
    schema = get_db_schema()
    with core_db.read_connection() as conn:
        row = select_latest_cycle_run(conn, request_id, schema=schema)
    if row is None:
        raise LookupError(f"실행이력을 찾을 수 없습니다: {request_id}")
    return cast(OrchestratorAgentRun, row)


def list_runs(
    *,
    agent: Agent | None = None,
    as_of: date | None = None,
    limit: int = 50,
) -> list[OrchestratorAgentRun]:
    """최신순 목록. 화면에서 그날 무슨 일이 있었는지 훑는 용도다."""
    bounded = max(1, min(limit, 200))
    schema = get_db_schema()
    with core_db.read_connection() as conn:
        rows = select_cycle_runs(conn, agent=agent, as_of=as_of, limit=bounded, schema=schema)
    return cast(list[OrchestratorAgentRun], rows)
