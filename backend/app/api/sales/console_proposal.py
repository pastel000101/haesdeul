"""운영 콘솔 판매 후보 생성 — `POST /sales/console-proposal`.

HTTP 만: 화면 요청을 봉투(`AgentRequest`)로 적어 `sales/service/proposal_generation.py`(마스터
어댑터와 같은 함수)를 부르고, 회신 봉투를 응답으로 그대로 돌려준다. 요청 모델은 이 입구만 쓴다.
"""

from datetime import date
from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.contracts.envelope import AgentReply, AgentRequest, ExecutionContext
from app.sales.schemas.proposal import SalesProposalInput
from app.sales.service.proposal_generation import generate_sales_proposal

router = APIRouter(prefix="/sales", tags=["sales"])


class ConsoleSalesProposalRequest(BaseModel):
    """운영 화면의 후보 생성 요청. 실행축은 봉투 context에만 보존한다."""

    sim_run_id: str = Field(min_length=1)
    as_of: date
    proposal: SalesProposalInput


@router.post("/console-proposal", summary="운영 콘솔 판매 후보 생성")
def create_console_sales_proposal(body: ConsoleSalesProposalRequest) -> AgentReply:
    """후보를 이력에 저장하지만 판매 원장을 생성하거나 승인하지 않는다."""
    #  마스터와 같은 판매 후보 생성(`sales/service/proposal_generation.py`)을 부른다 — 화면
    #    요청을 같은 봉투 모양으로 적어 넘기고, 회신 봉투를 응답으로 그대로 돌려준다.
    request = AgentRequest(
        context=ExecutionContext(
            request_id=f"CONSOLE-SALES-{uuid4()}",
            as_of=body.as_of,
            trigger="USER_REQUEST",
            policy_version="console-v1",
            sim_run_id=body.sim_run_id,
        ),
        agent="sales",
        mode="GENERATE_SALES_PROPOSAL",
        payload=body.proposal.model_dump(mode="json", exclude={"execution_identity"}),
    )
    return generate_sales_proposal(request).reply
