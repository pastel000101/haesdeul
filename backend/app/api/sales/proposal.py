"""판매 시나리오 제안 — `POST /sales/proposal` (이력 저장 없음).

HTTP 만: `sales/service/proposal.py` 를 부른다.

★ 2026-09-30 재구성 BL-019: `app/sales/router.py` 에서 옮겼다 — 핸들러 이름 · docstring(OpenAPI
  설명) · URL · 응답 그대로.
"""

from fastapi import APIRouter

from app.sales.schemas.proposal import SalesProposalInput, SalesProposalReply
from app.sales.service.proposal import run_proposal

router = APIRouter(prefix="/sales", tags=["sales"])


@router.post(
    "/proposal",
    response_model=SalesProposalReply,
    summary="영업 판매 시나리오 제안",
)
def review_sales_proposal(request: SalesProposalInput) -> SalesProposalReply:
    """Master 연동 전 Sales 전용 시나리오 생성·해석 경로다."""
    return run_proposal(request)
