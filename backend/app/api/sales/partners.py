"""거래처 — `POST /sales/partners` · `GET · PATCH /sales/partners/{partner_id}/profile`.

HTTP 만: 쓰기는 `sales/service/partners.py`(마스터 ask 와 같은 함수), 조회는
`sales/readmodel/partners.py`. service 가 낸 업무 예외를 상태 코드로 바꾼다.

★ 2026-09-30 재구성 BL-019: `app/sales/router.py` 에서 옮겼다 — 핸들러 이름 · docstring(OpenAPI
  설명) · URL · 상태 코드 · 문구 그대로.
"""

from fastapi import APIRouter, HTTPException, status

from app.sales.readmodel.partners import get_partner_profile
from app.sales.schemas.partners import (
    PartnerAlreadyExists,
    PartnerInputRejected,
    PartnerNotFound,
    PartnerProfile,
)
from app.sales.service.partners import create_partner, update_partner

router = APIRouter(prefix="/sales", tags=["sales"])


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
