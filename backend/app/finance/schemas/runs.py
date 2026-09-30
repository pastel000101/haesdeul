"""재무 에이전트 실행이력 — 저장 행과 조회 응답.

★ 2026-09-29 재구성 BL-014: 저장 행 모양(`FinanceAgentRun`)은 `finance/execution.py`, 조회 응답
  (`FinanceAgentRunResponse`)은 `finance/schemas.py` 에서 옮겼다. 저장은 `service/run_history.py`,
  조회는 `readmodel/runs.py`, SQL 은 `repository/runs.py` 다.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TypedDict
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.finance.schemas.agent import FinalVerdict, FinanceCycle, RuntimeStatus

# ---------------------------------------------------------------------------
# 실행이력 조회 응답
# ---------------------------------------------------------------------------

class FinanceAgentRunResponse(BaseModel):
    """UI 조회용 Finance Agent 실행이력 응답."""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    cycle: FinanceCycle
    as_of: date
    snapshot_id: str | None
    runtime_status: RuntimeStatus
    verdict: FinalVerdict | None
    request_payload: dict[str, object]
    response_payload: dict[str, object]
    created_at: datetime


# ---------------------------------------------------------------------------
# 실행이력 — 저장(append-only)과 조회
# ---------------------------------------------------------------------------

class FinanceAgentRun(TypedDict):
    run_id: UUID
    cycle: FinanceCycle
    as_of: date
    snapshot_id: str | None
    runtime_status: RuntimeStatus
    verdict: FinalVerdict | None
    request_payload: dict[str, object]
    response_payload: dict[str, object]
    created_at: datetime
