"""판매 실행이력 계약 — 실행 종류 · 런타임 상태 어휘와 저장 · 조회 모양.

★ 2026-09-29 BL-013: `sales/schemas.py` 의 어휘 둘과 응답 모델, `sales/runs.py` 의 행 모양을
  여기로 모았다. 저장 SQL 은 `repository/runs.py`, 조회 조립은 `readmodel/runs.py` 다.
"""

from datetime import date, datetime
from typing import Literal, TypedDict
from uuid import UUID

from pydantic import BaseModel, ConfigDict

SalesCycle = Literal["PROCUREMENT", "SALES"]
RuntimeStatus = Literal["READY", "RUNTIME_NOT_READY", "ERROR"]


# ---------------------------------------------------------------------------
# 실행이력 조회
# ---------------------------------------------------------------------------


class SalesAgentRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    cycle: SalesCycle
    as_of: date
    snapshot_id: str | None
    runtime_status: RuntimeStatus
    request_payload: dict[str, object]
    response_payload: dict[str, object]
    created_at: datetime


class SalesAgentRun(TypedDict):
    run_id: UUID
    cycle: SalesCycle
    as_of: date
    snapshot_id: str | None
    runtime_status: RuntimeStatus
    request_payload: dict[str, object]
    response_payload: dict[str, object]
    created_at: datetime
