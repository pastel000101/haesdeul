"""재무 에이전트 실행이력 — `GET /finance/runs` · `GET /finance/runs/{run_id}`.

HTTP 만: 조회는 `finance/readmodel/runs.py` 가 한다.
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.finance.domain import messages
from app.finance.readmodel.runs import get_finance_run, list_finance_runs
from app.finance.schemas.agent import FinalVerdict, FinanceCycle, RuntimeStatus
from app.finance.schemas.runs import FinanceAgentRunResponse

router = APIRouter(prefix="/finance", tags=["finance"])


@router.get(
    "/runs",
    response_model=list[FinanceAgentRunResponse],
    summary="Finance Agent 실행이력 목록 조회",
)
def get_finance_runs(
    cycle: FinanceCycle | None = None,
    as_of: date | None = None,
    runtime_status: RuntimeStatus | None = None,
    verdict: FinalVerdict | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[FinanceAgentRunResponse]:
    """cycle, as_of, runtime_status 필터로 최근 실행이력을 반환한다."""
    return list_finance_runs(
        cycle=cycle,
        as_of=as_of,
        runtime_status=runtime_status,
        verdict=verdict,
        limit=limit,
    )


@router.get(
    "/runs/{run_id}",
    response_model=FinanceAgentRunResponse,
    summary="Finance Agent 실행이력 단건 조회",
)
def get_finance_run_by_id(run_id: UUID) -> FinanceAgentRunResponse:
    """run_id에 해당하는 실행이력을 반환한다."""
    try:
        return get_finance_run(run_id)
    except LookupError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=messages.RUN_NOT_FOUND,
        ) from error
