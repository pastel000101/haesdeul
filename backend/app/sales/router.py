"""영업 Agent API Router.

★ 2026-09-29 BL-013: **HTTP 만 남겼다** — 요청 모델 → service · readmodel → 응답, 업무 예외 →
  상태 코드. 거래처 쓰기는 `service/partners.py`(마스터 ask 도 같은 함수), 판매 후보 생성은
  `service/proposal_generation.py`(마스터 어댑터도 같은 함수), 판매 제안은
  `service/proposal.py`, 조회는 `readmodel/` 이다. 라우트를 `api/sales/` 로 옮기는 것은
  BL-019 다(URL 그대로).
"""

from datetime import date
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.contracts.envelope import AgentReply, AgentRequest, ExecutionContext
from app.sales.readmodel.partners import get_partner_profile
from app.sales.readmodel.runs import get_sales_run, list_sales_runs
from app.sales.schemas.partners import (
    PartnerAlreadyExists,
    PartnerInputRejected,
    PartnerNotFound,
    PartnerProfile,
)
from app.sales.schemas.proposal import SalesProposalInput, SalesProposalReply
from app.sales.schemas.runs import RuntimeStatus, SalesAgentRunResponse, SalesCycle
from app.sales.service.partners import create_partner, update_partner
from app.sales.service.proposal import run_proposal
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
    #  ★ 마스터와 **같은 판매 후보 생성**(`service/proposal_generation.py`)을 부른다 — 화면
    #    요청을 같은 봉투 모양으로 적어 넘기고, 회신 봉투를 응답으로 그대로 돌려준다. 전에는
    #    여기서 마스터 어댑터(`sales_port`)를 직접 불렀다 (2026-09-29 BL-013). docstring 은
    #    OpenAPI 설명이라 그대로 둔다.
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


@router.post(
    "/proposal",
    response_model=SalesProposalReply,
    summary="영업 판매 시나리오 제안",
)
def review_sales_proposal(request: SalesProposalInput) -> SalesProposalReply:
    """Master 연동 전 Sales 전용 시나리오 생성·해석 경로다."""
    return run_proposal(request)


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


@router.post(
    "/partners",
    response_model=PartnerProfile,
    status_code=status.HTTP_201_CREATED,
    summary="거래처 등록",
)
def add_partner_profile(body: dict[str, object]) -> PartnerProfile:
    """새 거래처를 만들고 **저장된 행**을 돌려준다.

    ★ 실행 축(`sim_run_id`)을 받지 않는다. 거래처는 실행과 무관한 원장 행이라
      «A 실행의 거래처» 라는 개념이 없다 — `update_partner_profile` 과 같은 규율이다.

    🔴 **여신 한도는 여기서 만들지 않는다.** 정본은 재무의 `partner_credit_limits`
       이고, 같은 이름의 칸을 거래처 행에 두면 두 곳이 다른 한도를 말하는 날이 온다.
    """
    try:
        return create_partner(body)
    except PartnerInputRejected as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    except PartnerAlreadyExists as error:
        #  🔴 409 다. 400 으로 내면 화면이 «입력이 틀렸다» 로 읽어 칸을 빨갛게 만든다 —
        #     틀린 것은 칸이 아니라 이미 그 코드가 쓰이고 있다는 사실이다.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error.message) from error


@router.get(
    "/partners/{partner_id}/profile",
    response_model=PartnerProfile,
    summary="거래처 기본정보 조회",
)
def read_partner_profile(partner_id: str) -> PartnerProfile:
    """거래처 원장 행 그대로. 🔴 여신 한도는 여기 없다 — 재무 정본이다."""
    profile = get_partner_profile(partner_id=partner_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="거래처를 찾지 못했습니다."
        )
    return profile


@router.patch(
    "/partners/{partner_id}/profile",
    response_model=PartnerProfile,
    summary="거래처 기본정보 수정",
)
def edit_partner_profile(
    partner_id: str, body: dict[str, object]
) -> PartnerProfile:
    """준 칸만 고치고 **저장된 결과**를 돌려준다.

    🔴 **남의 도메인 값은 조용히 무시하지 않고 거절한다.** 무시하면 사용자는 고쳐진
       줄 알고 화면을 닫는다 — 여신 한도가 특히 그렇다.
    """
    try:
        return update_partner(partner_id, body)
    except PartnerInputRejected as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    except PartnerNotFound as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error.message) from error
