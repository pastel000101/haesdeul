"""매입 · 판매 Flow 실행 — `POST /master/request` · `/master/sales/run` · `/master/trigger`.

정의서 v2.2 §3.1 — 진입점은 사용자 요청과 ML 완료 Trigger 둘이다.

마스터는 도메인 DB 를 읽지 않는다 (§3.2.5). 각 에이전트가 자기 Tool 로 조회하고,
마스터는 요청 본문과 실행 이력만 다룬다.

HTTP 만: 요청 모델 → `master/service/procurement.run_procurement` · `master/service/sales.run_sales`
(하루 시뮬레이션 · 발화문 실행과 같은 함수) → 응답, 계약 위반 → 422.
"""

from fastapi import APIRouter, HTTPException, status

from app.contracts.core import ContractViolation
from app.master.schemas.procurement import ProcurementRunRequest, ProcurementRunResponse, TriggerAck
from app.master.schemas.sales import SalesRunRequest, SalesRunResponse
from app.master.service.procurement import run_procurement
from app.master.service.sales import run_sales

router = APIRouter(prefix="/master", tags=["master"])


@router.post(
    "/request",
    response_model=ProcurementRunResponse,
    summary="매입 의사결정 Flow 실행",
)
def master_request(request: ProcurementRunRequest) -> ProcurementRunResponse:
    """재무 · 물류 경계 수집 → 매입 시나리오 → 재검증 → 사용자 선택지.

    실패도 200 으로 돌려준다. 부서 미가동 · 보류 · 반려는 오류가 아니라 그날의 결과이며
    종료 코드로 구분된다. 422 는 요청 자체가 계약을 어긴 경우다.
    """
    try:
        return run_procurement(request)
    except ContractViolation as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error


@router.post(
    "/sales/run",
    response_model=SalesRunResponse,
    summary="판매 의사결정 Flow 실행",
)
def master_sales_run(request: SalesRunRequest) -> SalesRunResponse:
    """물류 초기 컨텍스트 → 판매 후보 → 후보별 검증 라우팅 → 사용자 선택지.

    실패도 200 으로 돌려준다(`/master/request` 와 같다). 후보 없음 · 전부 탈락 · 미시작 ·
    예산 소진 · 판정 미완료는 오류가 아니라 그날의 결과이며 `SL1`~`SL6` 종료 코드로
    구분된다. 422 는 요청 자체가 계약을 어긴 경우다.

    주말에도 돈다. 개장 확인은 거치지만 실행일 확인은 매입에만 적용된다. 판매에는 ML
    예측이 필요하지 않기 때문이다.

    판매에는 비동기 진입점(`/master/sales/trigger`)이 없다. 매입의 `/master/trigger` 는 ML
    완료 이벤트를 받는 용도이고, 판매는 사람이 요청해서 시작한다.

    `trigger` 의 기본값은 판매도 `USER_REQUEST` 다. `ML_COMPLETE` 는 판매 경로에서
    의미가 없지만 어휘를 매입과 같게 유지하려고 따로 만들지 않았다.
    """
    try:
        return run_sales(request)
    except ContractViolation as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error


@router.post(
    "/trigger",
    response_model=TriggerAck,
    summary="ML 예측 완료 이벤트 수신",
)
def master_trigger(request: ProcurementRunRequest) -> TriggerAck:
    """ML 파이프라인이 "오늘 예측 · 적재가 끝났다" 를 알리면 매입 Flow 를 실행한다.

    마스터는 ML 을 호출하지 않고 완료 신호만 받는다. 요청의 `trigger` 는
    `ML_COMPLETE` 로 바꿔 실행한다.

    예측값은 마스터가 매입에 실어 준다. 매입은 ML 데이터를 직접 읽지 않는다. 마스터는
    예측의 `generated_at` 을 `as_of` 와 대조한다(`envelope.forecast_is_clean`).

    제약: 큐 없이 동기로 즉시 실행한다. 그래서 응답의 `note` 는 항상 `executed` 다.
    """
    payload = request.model_copy(update={"trigger": "ML_COMPLETE"})
    result = run_procurement(payload)
    return TriggerAck(
        accepted=True,
        request_id=result.request_id,
        as_of=result.as_of,
        note="executed",
    )
