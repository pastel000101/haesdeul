"""재무 에이전트 실행이력 조회 (`GET /finance/runs*` · `/finance/agent/runs/{id}`).

★ 2026-09-29 재구성 BL-014: `finance/execution.py` 에서 옮겼다. 조회 연결 하나로 읽는다.
"""

from __future__ import annotations

from datetime import date
from typing import cast
from uuid import UUID

from app.core import db as core_db
from app.finance.repository.runs import (
    select_finance_agent_run,
    select_finance_agent_runs,
    select_finance_execution,
)
from app.finance.schemas.agent import FinalVerdict, FinanceCycle, RuntimeStatus
from app.finance.schemas.runs import FinanceAgentRun, FinanceAgentRunResponse


def get_finance_agent_run(run_id: UUID) -> FinanceAgentRun:
    """run_id로 Finance Agent 실행이력 한 건을 조회한다."""
    with core_db.read_connection() as conn:
        row = select_finance_agent_run(conn, run_id)
    if row is None:
        raise LookupError(f"Finance Agent run was not found: {run_id}")
    return cast(FinanceAgentRun, row)


def list_finance_agent_runs(
    *,
    cycle: FinanceCycle | None = None,
    as_of: date | None = None,
    runtime_status: RuntimeStatus | None = None,
    verdict: FinalVerdict | None = None,
    limit: int = 100,
) -> list[FinanceAgentRun]:
    """선택한 필터로 최신 Finance Agent 실행이력을 조회한다."""
    with core_db.read_connection() as conn:
        rows = select_finance_agent_runs(
            conn,
            cycle=cycle,
            as_of=as_of,
            runtime_status=runtime_status,
            verdict=verdict,
            limit=limit,
        )
    return cast(list[FinanceAgentRun], rows)


def get_finance_execution(run_id: UUID) -> dict[str, object]:
    with core_db.read_connection() as conn:
        row = select_finance_execution(conn, run_id)
    if row is None:
        raise LookupError(f"Finance v2.2 run was not found: {run_id}")
    return row


# ---------------------------------------------------------------------------
# 실행이력 조회 서비스 (UI · `/finance/runs`)
# ---------------------------------------------------------------------------

def get_finance_run(run_id: UUID) -> FinanceAgentRunResponse:
    """UI 조회용 Finance Agent 실행이력 한 건을 반환한다."""
    return FinanceAgentRunResponse.model_validate(get_finance_agent_run(run_id))


def list_finance_runs(
    *,
    cycle: FinanceCycle | None = None,
    as_of: date | None = None,
    runtime_status: RuntimeStatus | None = None,
    verdict: FinalVerdict | None = None,
    limit: int = 100,
) -> list[FinanceAgentRunResponse]:
    """UI 조회용 Finance Agent 실행이력 목록을 반환한다."""
    rows = list_finance_agent_runs(
        cycle=cycle,
        as_of=as_of,
        runtime_status=runtime_status,
        verdict=verdict,
        limit=limit,
    )
    return [FinanceAgentRunResponse.model_validate(row) for row in rows]
