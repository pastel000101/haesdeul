"""오케스트레이터 · Critic 실행이력(`orchestrator_agent_runs`) SQL. 받은 연결로 실행한다.

코어의 DB 미접근 원칙(§5.1)은 그대로다. 여기는 계산이 끝난 뒤 요청·응답을 적는 감사
기록이며, 저장한 값을 계산 입력으로 되읽지 않는다.

실패 처리: 적재 실패가 API 를 죽이지 않는다. 이력이 없는 것보다 결과를 못 주는 것이
나쁘다 — 저장은 `service/cycle_persistence.py` 의 `try_save_run` 을 통해 best-effort 로 부른다.

Finance / Logistics 의 `repository/runs.py` 와 같은 모양이되, `agent` 축이 하나 더 있다.

연결은 부르는 쪽이 호출마다 빌린다: 조회는 `readmodel/cycle_runs.py`(조회 연결), 적재는
`service/cycle_persistence.py`(연결 하나 · 트랜잭션 하나). 행 모양 · 어휘는 `schemas/cycle.py`.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from psycopg import sql
from psycopg.types.json import Jsonb

from app.master.schemas.cycle import Agent, RunCycle

_TABLE = "orchestrator_agent_runs"
_COLUMNS = (
    "run_id",
    "agent",
    "cycle",
    "as_of",
    "run_seq",
    "snapshot_id",
    "runtime_status",
    "critic_status",
    "coverage_ran",
    "coverage_total",
    "llm_status",
    "llm_model",
    "llm_attempts",
    "llm_fallback_used",
    "elapsed_ms",
    "request_payload",
    "response_payload",
    "request_id",
    "plan",
    "created_at",
)


def _select(schema: str) -> sql.Composed:
    return sql.SQL("SELECT {} FROM {}.{}").format(
        sql.SQL(", ").join(sql.Identifier(c) for c in _COLUMNS),
        sql.Identifier(schema),
        sql.Identifier(_TABLE),
    )


def insert_cycle_run(
    conn: Any,
    *,
    schema: str,
    run_id: UUID,
    agent: Agent,
    cycle: RunCycle,
    as_of: date,
    request_payload: dict[str, object],
    response_payload: dict[str, object],
    run_seq: int,
    snapshot_id: str | None,
    runtime_status: str,
    critic_status: str | None,
    coverage_ran: int | None,
    coverage_total: int | None,
    llm_status: str | None,
    llm_model: str | None,
    llm_attempts: int | None,
    llm_fallback_used: bool | None,
    elapsed_ms: int | None,
    request_id: str | None,
    plan: list[dict[str, object]] | None,
) -> dict[str, Any]:
    """실행 1건 INSERT … RETURNING. 행이 안 나오면 예외다."""
    query = sql.SQL(
        """
        INSERT INTO {}.{} (
            run_id, agent, cycle, as_of, run_seq, snapshot_id, runtime_status,
            critic_status, coverage_ran, coverage_total,
            llm_status, llm_model, llm_attempts, llm_fallback_used, elapsed_ms,
            request_payload, response_payload, request_id, plan
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING {}
        """
    ).format(
        sql.Identifier(schema),
        sql.Identifier(_TABLE),
        sql.SQL(", ").join(sql.Identifier(c) for c in _COLUMNS),
    )
    with conn.cursor() as cursor:
        cursor.execute(
            query,
            (
                run_id,
                agent,
                cycle,
                as_of,
                run_seq,
                snapshot_id,
                runtime_status,
                critic_status,
                coverage_ran,
                coverage_total,
                llm_status,
                llm_model,
                llm_attempts,
                llm_fallback_used,
                elapsed_ms,
                Jsonb(request_payload),
                Jsonb(response_payload),
                request_id,
                Jsonb(plan) if plan is not None else None,
            ),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Database write did not return a row")
        return row


def select_cycle_run(conn: Any, run_id: UUID, *, schema: str) -> dict[str, Any] | None:
    """UUID 로 1건. 없으면 `None`."""
    query = _select(schema) + sql.SQL(" WHERE run_id = %s")
    with conn.cursor() as cursor:
        cursor.execute(query, (run_id,))
        return cursor.fetchone()


def select_latest_cycle_run(conn: Any, request_id: str, *, schema: str) -> dict[str, Any] | None:
    """마스터 업무 키로 최신 1건. 없으면 `None`."""
    query = _select(schema) + sql.SQL(" WHERE request_id = %s ORDER BY created_at DESC LIMIT 1")
    with conn.cursor() as cursor:
        cursor.execute(query, (request_id,))
        return cursor.fetchone()


def select_cycle_runs(
    conn: Any, *, agent: Agent | None, as_of: date | None, limit: int, schema: str
) -> list[dict[str, Any]]:
    """주어진 조건만 AND 로 붙인 최신순 목록. `limit` 은 부르는 쪽이 이미 1~200 으로 좁혔다."""
    clauses: list[sql.Composable] = []
    params: list[object] = []
    if agent is not None:
        clauses.append(sql.SQL("agent = %s"))
        params.append(agent)
    if as_of is not None:
        clauses.append(sql.SQL("as_of = %s"))
        params.append(as_of)

    query = _select(schema)
    if clauses:
        query = query + sql.SQL(" WHERE ") + sql.SQL(" AND ").join(clauses)
    query = query + sql.SQL(" ORDER BY created_at DESC LIMIT %s")
    params.append(limit)
    with conn.cursor() as cursor:
        cursor.execute(query, tuple(params))
        return cursor.fetchall()
