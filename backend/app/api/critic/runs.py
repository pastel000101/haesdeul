"""Critic 실행이력 — `GET /critic/runs` · `GET /critic/runs/{run_id}`.

HTTP 만: 조회는 `master/readmodel/cycle_runs.py` 가 한다.
"""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.master.readmodel.cycle_runs import get_run, list_runs

router = APIRouter(prefix="/critic", tags=["critic"])


@router.get("/runs", summary="Critic 실행이력 목록 (최신순)")
def list_critic_runs(
    as_of: date | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict]:
    return [dict(r) for r in list_runs(agent="critic", as_of=as_of, limit=limit)]


@router.get("/runs/{run_id}", summary="Critic 실행이력 1건")
def get_critic_run(run_id: UUID) -> dict:
    try:
        return dict(get_run(run_id))
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
