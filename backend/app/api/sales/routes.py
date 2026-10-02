"""판매 탭 주소. 소유: 판매 파트."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.sales.presenter import build
from app.api.sales.schema import SalesTab
from app.core.settings import screen_sim_run_id

router = APIRouter(prefix="/sales", tags=["api:sales"])


@router.get("", response_model=SalesTab, summary="판매 탭 (조회 전용)")
def sales_tab(
    as_of: Annotated[date, Query(description="기준일")],
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
) -> SalesTab:
    return build(as_of, sim_run_id=screen_sim_run_id(sim_run_id))
