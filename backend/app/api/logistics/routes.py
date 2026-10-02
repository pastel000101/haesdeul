"""재고 · 물류 탭 주소. 소유: 물류 파트."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.logistics.presenter import PANES, build_result
from app.api.logistics.schema import LogisticsTab
from app.core.settings import screen_sim_run_id

router = APIRouter(prefix="/logistics", tags=["api:logistics"])


@router.get("", response_model=LogisticsTab, summary="재고 · 물류 탭")
def logistics_tab(
    as_of: Annotated[date, Query(description="기준일")],
    response: Response,
    pane: Annotated[str, Query(description="안쪽 작은 탭")] = "summary",
    sim_run_id: Annotated[
        str | None,
        Query(
            min_length=1,
            description=(
                "화면이 보는 실행 ID(sim_runs.sim_run_id). 화면은 GET /api/console/shown-run 이"
                " 준 값을 싣습니다. 안 주면 같은 백엔드 기준값을 씁니다"
            ),
        ),
    ] = None,
) -> LogisticsTab:
    """재고 · 물류 탭 한 화면 분량을 기준일 기준으로 돌려준다.

    읽기 실패는 `200 OK` 로 내보내지 않는다. 본문에 `Source.status="ERROR"` 를 적어도
    HTTP 가 200 이면 그 응답이 성공으로 캐시 · 집계되고 재시도되지 않기 때문이다.

    ```text
    OK · NO_DATA          200   읽었다. 값이 있거나, 그날 기록이 없다
    ERROR · DB 접속 실패   503   지금은 읽을 수 없다 — 다시 시도하면 될 수 있다
    ERROR · 그 밖          500   이 요청 처리 중 오류가 났다
    ```

    오류일 때도 본문은 `LogisticsTab` 모양 그대로다. `{"detail": …}` 만 돌려주면 화면이
    실패 이유를 그릴 수 없으므로, 상태 코드만 바꾸고 본문은 그대로 준다.

    없는 `pane` 은 요청이 틀린 것이라 400 이고, 이때는 `{"detail": …}` 만 돌려준다.
    """
    if pane not in PANES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"없는 화면입니다: {pane}. 가능: {', '.join(PANES)}",
        )
    result = build_result(as_of, pane, sim_run_id=screen_sim_run_id(sim_run_id))
    response.status_code = result.http_status
    return result.tab
