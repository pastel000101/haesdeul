"""재무 에이전트 직접 실행 — `POST /finance/agent` · `GET /finance/agent/runs/{run_id}`.

예외 (사용자 결정): `POST /finance/agent` 는 요청 · 응답이 봉투 그 자체라 라우트가 마스터와
같은 입구(`finance/adapter.finance_port`)를 부른다. 어댑터를 들이는 곳은 이 파일과 마스터 등록소
조립 둘뿐이다. 없앨지는 아직 정하지 않았다.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.contracts.envelope import AgentReply, AgentRequest
from app.finance.adapter import finance_port
from app.finance.domain import messages
from app.finance.readmodel.runs import get_finance_execution

router = APIRouter(prefix="/finance", tags=["finance"])


@router.post("/agent", summary="Finance v2.2 Tool-Using Agent")
def run_finance_agent(request: AgentRequest) -> AgentReply:
    """Master와 동일한 Finance Port를 통해 Agent를 실행한다."""
    reply, _metadata = finance_port(request)
    return reply


@router.get("/agent/runs/{run_id}", summary="Finance v2.2 execution metadata")
def get_finance_execution_by_id(run_id: UUID) -> dict[str, object]:
    try:
        return get_finance_execution(run_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=messages.RUN_NOT_FOUND) from error
