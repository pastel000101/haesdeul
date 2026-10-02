"""운영 콘솔의 부서 공통 조회 — 실행 목록과 화면이 보는 실행 ID."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.console.schema import ShownRunOut
from app.core.settings import shown_sim_run_id
from app.master.readmodel.console_runs import ConsoleRunsResponse, get_console_runs

router = APIRouter(prefix="/console", tags=["console"])


@router.get("/runs", response_model=ConsoleRunsResponse)
def runs(limit: Annotated[int, Query(ge=1, le=500)] = 100) -> ConsoleRunsResponse:
    """콘솔에서 고를 수 있는 실행 목록. 읽기 전용이며 실행을 만들거나 고치지 않는다."""
    return get_console_runs(limit=limit)


@router.get("/shown-run", response_model=ShownRunOut)
def shown_run() -> ShownRunOut:
    """화면이 조회와 업무 실행에 함께 쓰는 실행 ID. 값은 백엔드 설정 한 자리에서 정한다.

    화면 탭 · 채팅 · 판매 진행 패널 · 하루 시뮬레이션이 이 값을 받아 요청에 싣는다. 실행
    ID 하나만 돌려주고 다른 설정은 싣지 않는다.
    """
    return ShownRunOut(sim_run_id=shown_sim_run_id())
