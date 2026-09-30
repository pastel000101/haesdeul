"""발화문 입구 — `POST /master/ask` · `POST /master/ask/execute`.

HTTP 만: `master/service/ask.py` 의 `ask` · `execute` 를 부르고, 업무 예외를 상태 코드로 바꾼다.

🔴 **`/master/ask` 는 읽기처럼 보여도 쓴다** — 바로 도는 조회(`STATUS_QUERY`)는 실행 이력
   (`master_agent_runs`)을 남긴다(`service/ask.py`). 이 입구는 그것을 바꾸지 않는다.

★ 2026-09-30 재구성 BL-019: `app/master/router.py` 에서 옮겼다 — 핸들러 이름 · docstring(OpenAPI
  설명) · URL · 상태 코드 · 문구 그대로.
"""

from fastapi import APIRouter, HTTPException, status

from app.master.schemas.ask import AskExecuteRequest, AskRequest, AskResponse
from app.master.schemas.decision import DecisionRejected
from app.master.schemas.procurement import ProcurementRunResponse
from app.master.service.ask import ask as run_ask
from app.master.service.ask import execute as run_ask_execute

router = APIRouter(prefix="/master", tags=["master"])


@router.post(
    "/ask",
    response_model=AskResponse,
    summary="발화문 입구 — 분류하고, 확인이 필요 없으면 조회까지",
)
def master_ask(request: AskRequest) -> AskResponse:
    """사용자의 말을 알아듣고 **무엇을 할지 정한다.** 마스터 역할 ①(요청 해석).

    ★ **분류와 실행이 다르다.** 확인이 필요하면 `CLASSIFIED_ONLY` 로 되묻고
      **아무것도 돌리지 않는다.** 200 으로 나가는 정상 경로다.

    ★ **바로 도는 것은 조회뿐이다.** 오분류 비용이 비대칭이라 그렇다 — 조회를 잘못
      고르면 다시 물으면 그만이지만, 매입은 호출 예산 12회와 매입 LLM 을 태운다.

    ★ **LLM 이 죽어도 200 이다.** 키·서버가 없으면 `llm_status=FALLBACK` 에
      `NEEDS_CLARIFICATION` 으로 되묻는다 — 브랜치만 받은 팀원 환경에서 깨지지 않는다.

    | outcome | 뜻 |
    |---|---|
    | `STATUS_ANSWERED` | 조회를 돌려 답을 담았다 |
    | `CLASSIFIED_ONLY` | 알아들었지만 확인이 필요해 실행하지 않았다 |
    | `NEEDS_CLARIFICATION` | 못 알아들었다 — 되묻는다 |
    """
    return run_ask(request)


@router.post(
    "/ask/execute",
    summary="확인한 의도를 실행 — 발화문을 다시 분류하지 않는다",
)
def master_ask_execute(request: AskExecuteRequest) -> AskResponse | ProcurementRunResponse:
    """`/ask` 가 돌려준 `intent` 를 **그대로** 보내 실행한다.

    ★ **재분류하지 않는다.** 다시 분류하면 사용자가 확인한 것과 다른 것이 돌 수 있고,
      그 순간 확인의 뜻이 사라진다. **본 것을 실행한다.**

    ★ 매입 실행은 기존 `/master/request` 와 **같은 Flow** 를 탄다 — 발화문 경로라고
      다른 조립을 두면 두 경로가 조용히 갈라진다 (구 백로그 B1-3 이 그 문제였다).

    ★ **`SELECT_SCENARIO` 는 `/runs/{id}/decision` 과 같은 규칙을 탄다.** 발화문
      경로라고 승인 기준을 따로 두면 화면에서 누른 승인과 말로 한 승인이 갈라진다.
      그래서 상태 코드도 같다 — 404 · 409 · 422.

      🔴 다만 **말에 없는 둘을 본문에 실어야 한다** — `target_request_id`(어느 실행의
      안인가)와 `decided_by`(누가 승인하는가). 없으면 422 다. 서버가 "가장 최근 실행"
      으로 추측하면 엉뚱한 날의 안을 승인할 수 있다.

    아직 배선되지 않은 종류는 501 이다 — `RERUN_WITH_CONDITION` 은 조건을 반영한
    `/master/request` 를 쓴다.
    """
    try:
        return run_ask_execute(request)
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
