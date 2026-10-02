"""영업 Agent 실행이력 조회 — 저장된 행을 응답 모델로 편다.

SQL 은 `repository/runs.py` 다. 조회 하나가 조회 연결 하나를 빌린다.
"""

from datetime import date
from uuid import UUID

from app.core import db as core_db
from app.sales.repository.runs import get_sales_agent_run, list_sales_agent_runs
from app.sales.schemas.runs import RuntimeStatus, SalesAgentRunResponse, SalesCycle


def get_sales_run(run_id: UUID) -> SalesAgentRunResponse:
    """실행이력 한 건. 없으면 `LookupError` 다."""
    with core_db.read_connection() as conn:
        row = get_sales_agent_run(conn, run_id)
    return SalesAgentRunResponse.model_validate(row)


def list_sales_runs(
    *,
    cycle: SalesCycle | None = None,
    as_of: date | None = None,
    snapshot_id: str | None = None,
    runtime_status: RuntimeStatus | None = None,
    limit: int = 100,
) -> list[SalesAgentRunResponse]:
    """고른 필터의 최신 실행이력."""
    with core_db.read_connection() as conn:
        rows = list_sales_agent_runs(
            conn,
            cycle=cycle,
            as_of=as_of,
            snapshot_id=snapshot_id,
            runtime_status=runtime_status,
            limit=limit,
        )
    return [SalesAgentRunResponse.model_validate(row) for row in rows]
