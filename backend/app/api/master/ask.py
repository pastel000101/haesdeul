"""발화문 입구 — `POST /master/ask` · `POST /master/ask/execute`.

HTTP 만: `master/service/ask.py` 의 `ask` · `execute` 를 부르고, 업무 예외를 상태 코드로 바꾼다.

주의: `/master/ask` 는 읽기처럼 보여도 쓴다 — 바로 도는 조회(`STATUS_QUERY`)는 실행 이력
(`master_agent_runs`)을 남긴다(`service/ask.py`). 이 입구는 그것을 바꾸지 않는다.
"""

from fastapi import APIRouter, HTTPException, status

from app.core.settings import screen_sim_run_id
from app.master.schemas.ask import AskExecuteRequest, AskRequest, AskResponse
from app.master.schemas.decision import DecisionRejected
from app.master.schemas.procurement import ProcurementRunResponse
from app.master.service.ask import ask as run_ask
from app.master.service.ask import execute as run_ask_execute

router = APIRouter(prefix="/master", tags=["master"])


def _with_sim_run_id[R: (AskRequest, AskExecuteRequest)](request: R) -> R:
    """실행 ID 를 비워 보낸 요청은 백엔드 기준값으로 채워 넘긴다. 준 값은 그대로 쓴다."""
    return request.model_copy(update={"sim_run_id": screen_sim_run_id(request.sim_run_id)})


@router.post(
    "/ask",
    response_model=AskResponse,
    summary="발화문 입구 — 분류하고, 확인이 필요 없으면 조회까지",
)
def master_ask(request: AskRequest) -> AskResponse:
    """사용자의 말을 분류하고 무엇을 할지 정한다. 마스터의 요청 해석 역할이다.

    분류와 실행은 다르다. 확인이 필요한 요청은 `CLASSIFIED_ONLY` 로 되묻고 아무것도
    실행하지 않는다. 이것도 200 으로 나가는 정상 결과다.

    확인 없이 바로 실행하는 것은 상태 조회(`STATUS_QUERY`)와 도메인 읽기 요청뿐이다.
    잘못 분류한 조회는 다시 물으면 되지만, 매입 실행은 호출 예산(기본 12회)과 매입 LLM
    을 쓰고 도메인 쓰기는 장부를 바꾸므로 확인을 먼저 받는다. 바로 실행한 상태 조회는
    실행 이력에 남는다.

    LLM 을 쓸 수 없어도 200 이다. 키나 서버가 없어 분류 호출이 실패하면
    `llm_status=FALLBACK` 으로, LLM 을 끈 설정이면 `llm_status=DISABLED` 로
    `NEEDS_CLARIFICATION` 을 돌려준다.

    | outcome | 뜻 |
    |---|---|
    | `STATUS_ANSWERED` | 조회를 실행해 답을 담았다 |
    | `DOMAIN_ACTION_ANSWERED` | 도메인 읽기 요청을 실행해 답을 담았다 |
    | `CLASSIFIED_ONLY` | 분류했지만 확인이 필요해 실행하지 않았다 |
    | `NEEDS_CLARIFICATION` | 분류하지 못했거나 필요한 정보가 없어 되묻는다 |
    """
    return run_ask(_with_sim_run_id(request))


@router.post(
    "/ask/execute",
    summary="확인한 의도를 실행 — 발화문을 다시 분류하지 않는다",
)
def master_ask_execute(request: AskExecuteRequest) -> AskResponse | ProcurementRunResponse:
    """`/ask` 가 돌려준 `intent` 를 그대로 받아 실행한다.

    발화문을 다시 분류하지 않는다. 다시 분류하면 사용자가 확인한 것과 다른 것이 실행될
    수 있다.

    매입 실행(`PROCUREMENT_RUN`)은 `/master/request` 와 같은 Flow 를 탄다. 발화문 경로에
    다른 조립을 두면 두 경로의 결과가 갈라진다.

    `SELECT_SCENARIO` 와 `RERUN_WITH_CONDITION` 은 `/master/runs/{request_id}/decision` 과
    같은 결정 기록 규칙을 탄다. 그래서 상태 코드도 같다 — 404 · 409 · 422.
    `RERUN_WITH_CONDITION` 은 조건부 재요청을 기록한 뒤 새 업무 키로 매입을 다시 실행한다.

    제약: 발화문에 없는 두 값을 본문에 실어야 한다 — `target_request_id`(어느 실행의
    안인가)와 `decided_by`(누가 결정하는가). 없으면 422 다. 서버가 "가장 최근 실행" 으로
    추측하면 다른 날의 안을 승인할 수 있기 때문이다.

    | 상태 | 언제 |
    |---|---|
    | 404 | 대상 실행이 없다 |
    | 409 | 지금 상태에서 그 결정을 받을 수 없다 |
    | 422 | 요청이 틀렸다 — 필요한 값 누락 · `UNKNOWN` 의도 · 도메인 요청 정보 부족 |
    | 501 | 실행 경로가 연결되지 않은 의도 종류다 |
    """
    try:
        return run_ask_execute(_with_sim_run_id(request))
    except NotImplementedError as error:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(error),
        ) from error
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
