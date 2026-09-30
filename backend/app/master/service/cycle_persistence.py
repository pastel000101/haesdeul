"""★ **`app/orchestrator/` 에서 옮겼다** (2026-09-07 · 지시). 옛 경로는 없다.

⚠️ **`cycle_` 는 이름이 겹쳐서 붙였다** — `app/master/persistence.py` 가 이미 있다.
  그대로 옮기면 덮어쓴다.

라우터가 계산 뒤에 실행이력을 적재하는 얇은 층.

★ 계산과 적재를 섞지 않는다. `service.py` 는 여전히 순수하고, 여기서만 DB 를 만진다.
  적재 실패는 응답을 막지 않는다 (`try_save_run` 이 삼킨다).

★ 2026-09-30 재구성 BL-018: `master/cycle_persistence.py` 에서 옮겼고,
  `master/cycle_run_repository.py`
  의 적재 쪽(`save_run` · `history_enabled` · `try_save_run`)을 함께 모았다. `save_run` 은 종전
  `execute_returning_one` 과 같은 경계(연결 하나 · 트랜잭션 하나 · 행이 없으면 예외로 rollback)를
  직접 연다. SQL 은 `repository/cycle_runs.py`, 조회는 `readmodel/cycle_runs.py`.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from datetime import date
from typing import Any, cast
from uuid import UUID, uuid4

from pydantic import BaseModel

from app.core import db as core_db
from app.core.settings import get_db_schema
from app.master.repository.cycle_runs import insert_cycle_run
from app.master.schemas.cycle import Agent, OrchestratorAgentRun, RunCycle

# 응답 모델 → (agent, cycle) 을 무엇으로 적을지. 응답이 스스로 밝히는 값만 쓴다.
_CYCLE_BY_AGENT: dict[str, str] = {
    "PROCUREMENT": "PROCUREMENT",
    "SALES": "SALES",
}


def _coverage(response: Any) -> tuple[int | None, int | None]:
    ratio = getattr(response, "coverage_ratio", None)
    if not ratio or len(tuple(ratio)) != 2:
        return None, None
    ran, total = tuple(ratio)
    return int(ran), int(total)


def record(
    func: Callable[[Any], BaseModel],
    request: BaseModel,
    *,
    agent: Agent,
    cycle: RunCycle,
) -> BaseModel:
    """계산을 돌리고, 끝난 뒤 요청·응답을 적재한다.

    소요 시간을 함께 남긴다 — LLM 이 붙은 뒤로 지연이 관측 대상이 됐다.
    """
    started = time.perf_counter()
    response = func(request)
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    ran, total = _coverage(response)
    # `/day` 는 자기 llm_status 가 없다. 매입 하위 응답의 것을 대표로 적는다.
    llm_source = getattr(response, "procurement", None) or response

    try_save_run(
        agent=agent,
        cycle=cycle,
        as_of=getattr(response, "as_of", None) or getattr(request, "as_of", None),
        run_seq=getattr(request, "run_seq", 1) or 1,
        snapshot_id=getattr(response, "snapshot_id", None),
        runtime_status=getattr(response, "runtime_status", "READY") or "READY",
        critic_status=getattr(response, "status", None) if agent == "critic" else None,
        coverage_ran=ran,
        coverage_total=total,
        llm_status=getattr(llm_source, "llm_status", None),
        llm_model=getattr(llm_source, "llm_model", None),
        llm_attempts=getattr(llm_source, "llm_attempts", None),
        llm_fallback_used=getattr(llm_source, "llm_fallback_used", None),
        elapsed_ms=elapsed_ms,
        request_payload=request.model_dump(mode="json"),
        response_payload=response.model_dump(mode="json"),
    )
    return response


logger = logging.getLogger(__name__)


def save_run(
    *,
    agent: Agent,
    cycle: RunCycle,
    as_of: date,
    request_payload: dict[str, object],
    response_payload: dict[str, object],
    run_seq: int = 1,
    snapshot_id: str | None = None,
    runtime_status: str = "READY",
    critic_status: str | None = None,
    coverage_ran: int | None = None,
    coverage_total: int | None = None,
    llm_status: str | None = None,
    llm_model: str | None = None,
    llm_attempts: int | None = None,
    llm_fallback_used: bool | None = None,
    elapsed_ms: int | None = None,
    request_id: str | None = None,
    plan: list[dict[str, object]] | None = None,
) -> OrchestratorAgentRun:
    """실행 1건을 적재한다.

    ★ `request_id`·`plan` 은 마스터 행에만 채워진다 (2026-08-27).
      오케·Critic 은 UUID 로 조회하지만 마스터는 **업무 키**(`REQ-20260827-0001`)로 찾는다.
      `plan` 을 응답 원문 안에 묻지 않고 컬럼으로 뺀 것은, 검증 Tool 의 ④ 실행 계획
      온전성 검사(M-16)가 **이것만** 읽기 때문이다.
    """
    schema = get_db_schema()  # ★ 종전처럼 문장을 짓고(스키마 이름) 나서 연결을 빌린다
    with core_db.connection() as conn, core_db.transaction(conn):
        row = insert_cycle_run(
            conn,
            schema=schema,
            run_id=uuid4(),
            agent=agent,
            cycle=cycle,
            as_of=as_of,
            request_payload=request_payload,
            response_payload=response_payload,
            run_seq=run_seq,
            snapshot_id=snapshot_id,
            runtime_status=runtime_status,
            critic_status=critic_status,
            coverage_ran=coverage_ran,
            coverage_total=coverage_total,
            llm_status=llm_status,
            llm_model=llm_model,
            llm_attempts=llm_attempts,
            llm_fallback_used=llm_fallback_used,
            elapsed_ms=elapsed_ms,
            request_id=request_id,
            plan=plan,
        )
    return cast(OrchestratorAgentRun, row)


def history_enabled() -> bool:
    """실행이력을 남길지.

    ★ pytest 안에서는 남기지 않는다. 표가 팀 공용 DB 에 있어, 테스트를 돌릴 때마다
      2ms 짜리 가짜 실행이 쌓여 진짜 이력을 덮는다(실측: 12행 중 10행이 테스트 산물이었다).
      `RUN_HISTORY_ENABLED=false` 로 수동으로도 끌 수 있다.
    """
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    return os.getenv("RUN_HISTORY_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def try_save_run(**kwargs: Any) -> UUID | None:
    """적재를 시도하되 실패해도 예외를 올리지 않는다.

    ★ DB 가 없거나 표가 아직 없어도 API 는 계산 결과를 돌려줘야 한다.
      이력이 없는 것보다 결과를 못 주는 것이 나쁘다.
    """
    if not history_enabled():
        return None
    try:
        return save_run(**kwargs)["run_id"]
    except Exception:
        logger.warning("실행이력 적재 실패 — 계산 결과는 정상 반환합니다", exc_info=True)
        return None
