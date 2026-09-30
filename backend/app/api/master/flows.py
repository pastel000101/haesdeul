"""매입 · 판매 Flow 실행 — `POST /master/request` · `/master/sales/run` · `/master/trigger`.

정의서 v2.2 §3.1 — 진입점은 **사용자 요청**과 **ML 완료 Trigger** 둘이다.

★ 마스터는 도메인 DB 를 읽지 않는다 (§3.2.5). 각 에이전트가 자기 Tool 로 조회하고,
  마스터는 요청 본문과 실행 이력만 다룬다.

HTTP 만: 요청 모델 → `master/service/procurement.run_procurement` · `master/service/sales.run_sales`
(하루 시뮬레이션 · 발화문 실행과 같은 함수) → 응답, 계약 위반 → 422.

★ 2026-09-30 재구성 BL-019: `app/master/router.py` 에서 옮겼다 — 핸들러 이름 · docstring(OpenAPI
  설명) · URL · 상태 코드 · 문구 그대로.
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
    """재무·물류 경계 수집 → 매입 시나리오 → 재검증 → 사용자 선택지.

    **실패도 200 으로 돌려준다.** 부서 미가동·보류·반려는 오류가 아니라 **그날의 결과**이며
    종료 코드로 구분된다 (§5.3). 400/422 는 요청 자체가 계약을 어긴 경우다.
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

    **실패도 200 이다** — `/master/request` 와 같은 태도다. 후보 없음·전부 탈락·
    미시작·예산 소진은 오류가 아니라 **그날의 결과**이며 `SL1`~`SL5` 로 구분된다 (§5.3).

    🔴 **주말에도 돈다.** 개장 관문은 지나지만 실행일 관문은 **매입만** 지난다 —
      파는 데는 ML 예측이 필요 없다 (설계 §1).

    🔴 **`/master/sales/trigger`(비동기 ack)를 두지 않는다.** 매입의 그것은 ML 완료
      이벤트를 받는 스케줄러용이고, **판매는 사람이 눌러서 시작한다.** 부를 사람이
      없는 진입점을 만들면 어휘만 늘고 아무도 안 쓴다.

    ★ `trigger` 는 판매도 `USER_REQUEST` 가 기본이다. `ML_COMPLETE` 는 판매 경로에
      의미가 없지만 **어휘를 새로 만들지 않는다** — 쓰지 않는 값이 있는 것과 어휘가
      갈리는 것 중 뒤가 더 비싸다.
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
    """ML 파이프라인이 "오늘 예측·적재가 끝났다"를 알린다.

    ★ 마스터는 **ML 을 호출하지 않는다.** 완료 신호만 받는다 (§3.1).

    ★ **예측값은 마스터가 실어 준다** (§3.2.5 예외 · 매입 파트 지적으로 뒤집음).
      ML 은 매입의 도메인이 아니라 매입이 직접 읽으면 §1.2-9 를 어기고, ML 은 호출
      구조 밖이라 §4.1 의 "해당 에이전트에게 요청"도 성립하지 않는다. 대신 마스터가
      `generated_at` 을 `as_of` 와 대조한다 (`envelope.forecast_is_clean`).

    ⚠️ **지금은 동기로 즉시 실행한다.** 회의 3.1 이 요구한 Queue·비동기는 별도 이슈다.
      그래서 `note` 가 항상 `executed` 이며, 큐가 붙으면 `queued` 가 나온다.
    """
    payload = request.model_copy(update={"trigger": "ML_COMPLETE"})
    result = run_procurement(payload)
    return TriggerAck(
        accepted=True,
        request_id=result.request_id,
        as_of=result.as_of,
        note="executed",
    )
