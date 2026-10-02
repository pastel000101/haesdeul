"""사람의 결정과 그 뒤 — `/master/runs/{request_id}/decision` · `commitment` · `purchase-record` ·
`decisions`.

HTTP 만: 결정은 `master/service/decision.record_decision`, 실매입 기록은
`master/service/purchase_record.record_purchase`, 조회는 `master/readmodel/`. 트랜잭션 경계는 그
service 들이 쥔다(결정 한 건 먼저 · 전이는 따로).
"""

from fastapi import APIRouter, HTTPException, status

from app.master.readmodel.approvals import current_commitment
from app.master.readmodel.decisions import list_decisions
from app.master.readmodel.purchase_record import get_purchase_record
from app.master.schemas.decision import CommitmentOut, DecisionIn, DecisionOut, DecisionRejected
from app.master.schemas.purchase_record import PurchaseRecordIn, PurchaseRecordOut
from app.master.schemas.transition import TransitionOut
from app.master.service.decision import record_decision
from app.master.service.purchase_record import record_purchase

router = APIRouter(prefix="/master", tags=["master"])


@router.post(
    "/runs/{request_id}/decision",
    response_model=DecisionOut,
    status_code=status.HTTP_201_CREATED,
    summary="사용자 결정 기록 — 승인 · 전체 거절 · 조건부 재요청 · 승인 취소",
)
def master_decide(request_id: str, body: DecisionIn) -> DecisionOut:
    """마스터가 제시한 안에 대한 사람의 결정을 적는다.

    결정은 `APPROVE`(승인) · `REJECT_ALL`(전체 거절) · `REQUEST_CHANGE`(조건부 재요청) ·
    `CANCEL`(승인 취소) 중 하나다. 번복은 덮어쓰지 않고 회차를 올려 새 행으로 남긴다.

    마스터 Flow 는 이 경로를 부를 수 없다. 승인 게이트를 Flow 의 툴 목록 밖에 두어
    마스터가 스스로 승인하지 못하게 한다.

    적재 실패를 삼키지 않는다(`/master/request` 와 다르다). 결정이 남지 않았는데 201 을
    돌려주면 승인 없이 실행된 것과 같아지기 때문이다.

    승인이면 선택된 안 하나를 그 실행의 `as_of` 로 다시 검증하고, 결과를 응답의
    `revalidation_outcome` 에 싣는다. 부서 호출이 들어가므로 승인 응답은 다른 결정보다
    느리다.

      ```text
      PASSED       통과했다               CONDITIONAL  새 조건이 붙었다 — 화면이 다시 받는다
      FAILED       재검증에서 막혔다        ERROR        재검증 자체를 못 돌렸다
      ```

    네 값 모두 201 이다. 재검증이 막혀도 사용자가 승인했다는 사실은 결정 행에 그대로
    남는다.

    승인 뒤 장부 반영은 사이클과 승인 경로에 따라 다르다.

      ```text
      판매               판매를 확정한다(`sale`) — 재검증이 PASSED 가 아니면 확정이 막힌다
      매입 · 사람 승인    전이를 부르지 않는다 — `transition.status` 가
                         `AWAITING_PURCHASE_RECORD` 이고 실매입 기록을 기다린다
      매입 · 자동 승인    승인 즉시 계획값으로 전이한다(`transition`)
      ```

    | 상태 | 언제 |
    |---|---|
    | 404 | 그 업무 키의 실행이 없다 |
    | 409 | 지금 상태에서 받을 수 없다 — 아래 참고 |
    | 422 | 요청이 틀렸다 — 제시되지 않은 안 · 라벨/조건 누락 |

    409 는 결정을 받지 않는 종료 코드(`E4` · `SL4` 등)에 결정했거나, 통과안이 없는
    실행(`E1` · `SL1` 이 아님)에 승인 · 취소했거나, 이미 승인이 선 실행에 다시 승인한
    경우다.
    """
    try:
        # 여기서 날짜를 정하지 않는다. 재검증이 설 날은 그 실행의
        #    날이고, 그것은 실행 이력 행이 들고 있다 — 진입점이 정하면 화면이 언제
        #    누르느냐에 따라 재검증이 딴 날로 돈다.
        return record_decision(request_id, body)
    except LookupError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except DecisionRejected as error:
        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT if error.conflict else status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=str(error),
        ) from error


@router.get(
    "/runs/{request_id}/commitment",
    response_model=CommitmentOut,
    summary="현재 승인이 만든 확정 입고 약정 (H1)",
)
def master_commitment(request_id: str) -> CommitmentOut:
    """현재 승인이 만든 확정 입고 약정. 물류 H1 미래 점유 계산의 입력이다.

    | 상태 | 언제 |
    |---|---|
    | 200 | 현재 결정이 승인이다 — 약정을 만들지 못했으면 `buildable=false` 로 나간다 |
    | 404 | 승인이 없다 — 결정이 없거나, 현재 결정이 거절 · 조건부 재요청이다 |

    `buildable=false` 를 404 로 바꾸지 않는다. "승인이 없다" 와 "승인했는데 약정을 만들지
    못했다" 는 부르는 쪽이 다르게 처리해야 한다. 앞은 물류를 부르지 않으면 되고, 뒤는
    사람이 확인해야 한다.
    """
    commitment = current_commitment(request_id)
    if commitment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"업무 키 {request_id} 에 유효한 승인이 없다 — 약정은 승인에서만 나온다.",
        )
    return commitment


@router.post(
    "/runs/{request_id}/purchase-record",
    response_model=TransitionOut,
    status_code=status.HTTP_201_CREATED,
    summary="실매입 기록 — 사람이 실제로 산 값으로 전이를 세운다",
)
def master_purchase_record(request_id: str, body: PurchaseRecordIn) -> TransitionOut:
    """사람 승인 뒤 실제로 산 값(실매입)을 기록한다.

    기록값으로 선정안을 다시 검증하고, 통과하면 그 값으로 매입 원장 · 매입채무 · 입고
    일정 전이를 세운다. 응답은 승인 응답의 `transition` 과 같은 모양이다. 자동 승인은
    승인 즉시 계획값으로 반영되므로 이 경로의 대상이 아니다.

    | 상태 | 언제 |
    |---|---|
    | 201 | 기록했다 — 전이 결과는 `status` 가 말한다 (`APPLIED` · `NOT_APPLIED` · `FAILED`) |
    | 404 | 유효한 승인이 없다 |
    | 409 | 자동 승인이다 · 현재 승인 회차가 아니다 · 이미 기록했다 · 선정안 약정이 없다 |
    | 422 | 본문이 틀렸다 — 회차 집합이 선정안과 다르다 · 수량/단가 0 · 도착일 < 매입일 |

    422 는 매입일이 승인 기준일보다 앞서거나 지급기일이 이미 마감된 날 이전인 경우,
    기록값 재검증을 통과하지 못한 경우에도 나간다. 구체적인 사유는 `detail` 에 실린다.
    """
    try:
        return record_purchase(request_id, body)
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except DecisionRejected as error:
        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT if error.conflict else status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=str(error),
        ) from error


@router.get(
    "/runs/{request_id}/purchase-record",
    response_model=PurchaseRecordOut,
    summary="실매입 기록 · 반영 상태 조회 (화면용)",
)
def master_purchase_record_status(request_id: str) -> PurchaseRecordOut:
    """선정안 회차(폼 기본값) · 기록(있으면) · 반영 상태.

    | 상태 | 언제 |
    |---|---|
    | 200 | 현재 결정이 매입 승인이다 |
    | 404 | 유효한 승인이 없다 |
    | 409 | 매입 승인이 아니다 |
    """
    try:
        return get_purchase_record(request_id)
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except DecisionRejected as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.get(
    "/runs/{request_id}/decisions",
    response_model=list[DecisionOut],
    summary="결정 이력 — 번복도 지우지 않고 남는다",
)
def master_decision_history(request_id: str) -> list[DecisionOut]:
    """한 요청에 붙은 결정 전부. 오래된 것부터이며 최신 하나가 `is_current` 다.

    실행이 없어도 빈 목록을 돌려준다. 이 경로는 결정이 없는 것과 실행이 없는 것을
    구분하지 않는다. 실행이 있는지는 `GET /master/runs/{request_id}` 가 404 로 답한다.
    """
    return list_decisions(request_id)
