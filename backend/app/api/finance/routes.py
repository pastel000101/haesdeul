"""재무 탭 주소. 소유: 재무 파트."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.finance.presenter import STATES, build
from app.api.finance.schema import FinanceTab
from app.core.settings import screen_sim_run_id

router = APIRouter(prefix="/finance", tags=["api:finance"])


@router.get("", response_model=FinanceTab, summary="재무 탭 (조회 전용)")
def finance_tab(
    as_of: Annotated[date, Query(description="기준일")],
    state: Annotated[str, Query(description="저장된 재무 기준 상태")] = "base",
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
) -> FinanceTab:
    if state not in STATES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"없는 재무 보기입니다: {state}. 가능: {', '.join(STATES)}",
        )
    try:
        return build(as_of, state, sim_run_id=screen_sim_run_id(sim_run_id))
    except LookupError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="이 기준일까지 확인할 수 있는 재무 상태가 없습니다.",
        ) from exc
