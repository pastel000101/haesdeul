"""마스터 실행 이력 저장 — 자기 연결 · 자기 트랜잭션으로 적재하고, 실패는 응답을 바꾸지 않는다.

★ 2026-09-30 재구성 BL-018: `master/run_repository.py` 에서 옮겼다 — `logger`, `save_run`,
  `history_enabled`, `try_save_run`. 종전에는 `master/db.py` 의 `execute_returning_one` 이 연결을
  빌려 한 호출 = 한 트랜잭션으로 적재했다. 이제 `save_run` 이 같은 경계(`connection()` +
  `transaction(conn)` — 정상 commit · 예외 rollback)를 직접 열고 SQL 은 `repository/runs.py` 가
  받은 연결로 실행한다. 원장 트랜잭션과 섞지 않는 자기 연결이다(설계서 §4 규칙 4).
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any
from uuid import UUID, uuid4

from app.core import db as core_db
from app.core.settings import get_db_schema
from app.master.repository.runs import insert_run
from app.master.schemas.runs import MasterAgentRun

logger = logging.getLogger(__name__)


def save_run(
    *,
    cycle: str,
    as_of: date,
    request_payload: dict[str, object],
    response_payload: dict[str, object],
    request_id: str | None = None,
    run_seq: int = 1,
    item: str | None = None,
    end_code: str | None = None,
    runtime_status: str = "READY",
    coverage_ran: int | None = None,
    coverage_total: int | None = None,
    elapsed_ms: int | None = None,
    plan: list[dict[str, object]] | None = None,
    sim_run_id: str | None = None,
) -> MasterAgentRun:
    """실행 1건을 적재한다.

    ★ `agent` 인자가 없다. 이 표는 마스터 전용이라 늘 같은 값이었고, 상수를
      컬럼으로 두면 "언젠가 다른 값이 들어올 수 있다" 로 읽힌다.

    ★ `item` · `end_code` 는 payload 안에도 있지만 컬럼으로도 받는다.
      "배추가 며칠째 E2 인가" 를 JSONB 를 파지 않고 보기 위해서다. 값을 여기서
      꺼내지 않고 **부르는 쪽이 준다** - 이 모듈이 payload 모양을 알면 응답
      스키마가 바뀔 때마다 적재가 흔들린다.

    ★ `sim_run_id` 는 **어느 장부 위에서 돌았나**다 (2026-09-08 · `Refs #150`).
      출처는 `ExecutionContext.sim_run_id` 하나이고, 값은 부르는 쪽이 실어 준다 -
      전역이나 환경변수에서 집으면 봉투에 실린 값과 표에 적힌 값이 갈린다.

      🔴 빈 문자열은 NULL 로 접는다 (`null_if_blank`). 기본값 `""` 는 *"아직 안
      실렸다"* 이지 값이 아니다.
    """
    schema = get_db_schema()  # ★ 종전처럼 문장을 짓고(스키마 이름) 나서 연결을 빌린다
    with core_db.connection() as conn, core_db.transaction(conn):
        row = insert_run(
            conn,
            schema=schema,
            run_id=uuid4(),
            cycle=cycle,
            as_of=as_of,
            request_payload=request_payload,
            response_payload=response_payload,
            request_id=request_id,
            run_seq=run_seq,
            item=item,
            end_code=end_code,
            runtime_status=runtime_status,
            coverage_ran=coverage_ran,
            coverage_total=coverage_total,
            elapsed_ms=elapsed_ms,
            plan=plan,
            sim_run_id=sim_run_id,
        )
    return row  # type: ignore[return-value]


def history_enabled() -> bool:
    """실행이력을 남길지.

    ★ **pytest 안에서는 남기지 않는다.** 표가 팀 공용 DB 에 있어, 테스트를 돌릴
      때마다 2ms 짜리 가짜 실행이 쌓여 진짜 이력을 덮는다 (옛 표에서 실측:
      12행 중 10행이 테스트 산물이었다). `RUN_HISTORY_ENABLED=false` 로 수동으로도
      끌 수 있다.
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
    """적재하고 `run_id` 를 돌려준다. **실패하면 `None` 이고 예외를 올리지 않는다.**

    ★ 이력이 없는 것보다 결과를 못 주는 것이 나쁘다. 다만 조용히 넘어가지는
      않는다 - 로그에 남긴다. 실패하면 그 실행은 결정이 가리킬 수 없고,
      `master_decisions.run_id` 가 NULL 을 허용하는 이유가 그것이다.
    """
    if not history_enabled():
        return None
    try:
        return save_run(**kwargs)["run_id"]
    except Exception:
        logger.exception("마스터 실행이력 적재 실패 - 응답은 그대로 나간다")
        return None
