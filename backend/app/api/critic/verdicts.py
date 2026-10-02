"""Critic 검증 실행 — `POST /critic/procurement` · `POST /critic/sales`.

Critic 은 오케 산출물을 검증만 한다 (숫자 불변). 요청 본문만으로 T3/S3 를 재현해 검증한다.
계약 위반(밴드 기여 방향 등)은 422 로 돌려준다.

HTTP 만: `master/critic/service.py` 의 검증을 `master/service/cycle_persistence.record` 로 감싸
부른다 — 실행 이력 한 건을 그 service 가 자기 연결로 남긴다(실패는 삼킨다).
"""

from fastapi import APIRouter, HTTPException, status

from app.contracts.core import ContractViolation
from app.master.critic.schemas import (
    CriticProcurementRequest,
    CriticSalesRequest,
    CriticVerdictOut,
)
from app.master.critic.service import run_critic_procurement, run_critic_sales
from app.master.service.cycle_persistence import record

router = APIRouter(prefix="/critic", tags=["critic"])


def _guard(func, request, *, cycle="A"):
    try:
        return record(func, request, agent="critic", cycle=cycle)
    except (ContractViolation, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error


@router.post(
    "/procurement",
    response_model=CriticVerdictOut,
    summary="Critic A - 매입 T3 결과 6레이어 검증",
)
def critic_procurement(request: CriticProcurementRequest) -> CriticVerdictOut:
    """부서 회신 · 매입 후보로 T3 를 재현하고, 대상 후보를 L0~L5 로 검증한다."""
    return _guard(run_critic_procurement, request, cycle="A")


@router.post(
    "/sales",
    response_model=CriticVerdictOut,
    summary="Critic B - 판매 검증 (L1 · L2 · L4-B · L5)",
)
def critic_sales(request: CriticSalesRequest) -> CriticVerdictOut:
    """요청 본문으로 S3 를 재현하고 대상 배분을 검증한다.

    회신 스냅샷 바인딩과 S3 전속 권한 침범(L1), 룩어헤드(L2), 출고 결합 재검산(L4-B),
    판단 근거의 논리 일관성(L5, LLM 판정)을 본다. 앞 계층에서 실패가 나오면 뒤 계층은
    돌리지 않는다. 입력이 없어 생략된 검사는 `skipped` 에 남는다.
    """
    return _guard(run_critic_sales, request, cycle="B")
