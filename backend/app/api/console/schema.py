"""운영 콘솔 공통 조회의 응답 모델."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ShownRunOut(BaseModel):
    """화면이 조회와 업무 실행에 함께 쓰는 실행 ID."""

    sim_run_id: str = Field(description="화면이 보는 실행 ID(sim_runs.sim_run_id)")
