"""판매 에이전트 실행이력 — `GET /sales/runs` · `GET /sales/runs/{run_id}`.

HTTP 만: 조회는 `sales/readmodel/runs.py` 가 한다.

★ 2026-09-30 재구성 BL-019: `app/sales/router.py` 에서 옮겼다 — 핸들러 이름 · docstring(OpenAPI
  설명) · URL · 상태 코드 · 문구 그대로.
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.sales.readmodel.runs import get_sales_run, list_sales_runs
from app.sales.schemas.runs import RuntimeStatus, SalesAgentRunResponse, SalesCycle

router = APIRouter(prefix="/sales", tags=["sales"])


@router.get(
    "/runs",
    response_model=list[SalesAgentRunResponse],
    summary="영업 Agent 실행이력 목록 조회",
)
def get_sales_runs(
    cycle: SalesCycle | None = None,
    as_of: date | None = None,
    snapshot_id: str | None = None,
    runtime_status: RuntimeStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[SalesAgentRunResponse]:
    """cycle, as_of, snapshot_id, runtime_status 필터로 최근 실행이력을 반환한다."""
    return list_sales_runs(
        cycle=cycle,
        as_of=as_of,
        snapshot_id=snapshot_id,
        runtime_status=runtime_status,
        limit=limit,
    )


@router.get(
    "/runs/{run_id}",
    response_model=SalesAgentRunResponse,
    summary="영업 Agent 실행이력 단건 조회",
)
def get_sales_run_by_id(run_id: UUID) -> SalesAgentRunResponse:
    """run_id에 해당하는 실행이력을 반환한다."""
    try:
        return get_sales_run(run_id)
    except LookupError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sales Agent run was not found",
        ) from error
