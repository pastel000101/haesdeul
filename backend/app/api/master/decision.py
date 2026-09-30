"""사람의 결정과 그 뒤 — `/master/runs/{request_id}/decision` · `commitment` · `purchase-record` ·
`decisions`.

HTTP 만: 결정은 `master/service/decision.record_decision`, 실매입 기록은
`master/service/purchase_record.record_purchase`, 조회는 `master/readmodel/`. 트랜잭션 경계는 그
service 들이 쥔다(결정 한 건 먼저 · 전이는 따로).

★ 2026-09-30 재구성 BL-019: `app/master/router.py` 에서 옮겼다 — 핸들러 이름 · docstring(OpenAPI
  설명) · URL · 상태 코드 · 문구 그대로.
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
    summary="사용자 결정 기록 — 승인 · 전체 거절 · 조건부 재요청",
)
def master_decide(request_id: str, body: DecisionIn) -> DecisionOut:
    """마스터가 제시한 안에 대한 **사람의 결정**을 적는다 (회의 미결정 12번).

    ★ **마스터 Flow 가 부를 수 없는 경로다.** 승인 게이트가 툴 목록 안에 있으면
      마스터가 스스로 통과시킬 수 있다 — 8/26 회의가 "툴 바깥에 두어 우회 불가하게"로
      정한 이유다. `flow.py` 는 이 모듈을 임포트하지 않는다.

    ★ **적재 실패를 삼키지 않는다** — `/request` 와 반대다. 실행 이력은 없어도 결과를
      줄 수 있지만, 결정이 안 남았는데 201 을 돌려주면 승인 없이 실행된 것과 같아진다.

    🔴 **승인은 이제 부서를 부른다** (M-4 · 2026-09-07). `APPROVE` 면 **그때 선택된
      1안만** 오늘 `as_of` 로 다시 검증하고, 그 결과가 응답의 `revalidation_outcome`
      에 실린다 (`revalidation.py`). 그래서 승인 응답이 **다른 결정보다 느리다** —
      개장 관문 한 번과 부서 호출 두어 번이 그 안에 있다.

      ```text
      PASSED       통과했다               CONDITIONAL  새 조건이 붙었다 — 화면이 다시 받는다
      FAILED       재검증에서 막혔다        ERROR        재검증 자체를 못 돌렸다
      ```

      ⚠️ **네 값 전부 201 이다.** 재검증이 막힌 것은 요청이 틀린 것도, 지금 상태에서
        결정을 못 받는 것도 아니다 — **사용자가 승인을 눌렀다는 사실**은 그대로 적힌다
        (설계 §0). 막혔다고 행을 안 쓰면 *"승인하려다 막혔다"* 가 사라진다.

      ⚠️ **`PASSED` 여도 아직 도메인 Write 가 안 흐른다** (M-5).

    | 상태 | 언제 |
    |---|---|
    | 404 | 그 업무 키의 실행이 없다 |
    | 409 | 지금 상태에서 받을 수 없다 — `E4` 에 결정 · `E1` 아닌 날 승인 · 같은 안 재승인 |
    | 422 | 요청이 틀렸다 — 제시되지 않은 안 · 라벨/조건 누락 |
    """
    try:
        # 🔴 **여기서 날짜를 안 정한다** (2026-09-09). 재검증이 설 날은 **그 실행의
        #    날**이고, 그것은 실행 이력 행이 들고 있다 — 진입점이 정하면 화면이 언제
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
    """물류 H1 미래 점유의 입력이 되는 약정. 전달 방식 ⓐ — 물류 회신 2026-09-01 합의.

    | 상태 | 언제 |
    |---|---|
    | 200 | 현재 결정이 승인이다 — 못 만든 약정도 `buildable=false` 로 **사실대로** 나간다 |
    | 404 | 승인이 없다 — 결정이 없거나, 현재 결정이 거절·조건부 재요청이다 |

    ★ `buildable=false` 를 404 로 접지 않는다. *"승인이 없다"* 와 *"승인했는데 약정을
      못 만들었다"* 는 다른 사실이고, 부르는 쪽이 다르게 행동해야 한다 — 앞은 물류를
      부르지 않으면 되고, 뒤는 사람이 봐야 한다.
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
    """사람 승인 뒤 **실제로 산 값**을 적는다 (설계 260915 안 A §4-3).

    ★ 적는 순간 그 값으로 매입 원장 · 매입채무 · 입고 일정 전이가 선다. 응답은 승인
      응답의 `transition` 과 같은 모양이다.

    | 상태 | 언제 |
    |---|---|
    | 201 | 기록했다 — 전이 결과는 `status` 가 말한다 (`APPLIED` · `NOT_APPLIED` · `FAILED`) |
    | 404 | 유효한 승인이 없다 |
    | 409 | 자동 승인이다 · 현재 승인 회차가 아니다 · 이미 기록했다 · 선정안 약정이 없다 |
    | 422 | 본문이 틀렸다 — 회차 집합이 선정안과 다르다 · 수량/단가 0 · 도착일 < 매입일 |
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

    ★ 실행이 없어도 **빈 목록**을 돌려준다. 결정이 없는 것과 요청이 없는 것을 여기서는
      구분하지 않는다 — 그 구분은 `GET /master/runs/{request_id}` 가 404 로 답한다.
    """
    return list_decisions(request_id)
