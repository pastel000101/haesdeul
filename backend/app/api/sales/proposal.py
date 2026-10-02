"""판매 시나리오 제안 — `POST /sales/proposal` (이력 저장 없음).

HTTP 만: `sales/service/proposal.py` 를 부른다.
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
    """마스터를 거치지 않고 판매 제안 그래프만 한 번 돌려 시나리오를 생성 · 해석한다. 실행 이력은
    저장하지 않는다."""
    return run_proposal(request)
