"""Logistics Agent 실행이력 전용 PostgreSQL Repository.

★ 2026-09-30 재구성 BL-015: 이 파일에는 SQL 만 남았다 — 저장의 연결 · 트랜잭션은
  `service/run_history.py`, 조회의 연결은 `readmodel/runs.py`, 행 모양은 `schemas/runs.py`
  (종전 `logistics/db.execute_returning_one` · `fetch_one` · `fetch_all` 을 쓰던 자리).
"""

from datetime import date
from typing import Any
from uuid import UUID, uuid4

from psycopg import sql
from psycopg.types.json import Jsonb

from app.logistics.repository.rows import get_db_schema
from app.logistics.schemas.agent import FinalVerdict, LogisticsCycle, RuntimeStatus

_SELECT_COLUMNS = sql.SQL(
    """
    SELECT
        run_id,
        cycle,
        as_of,
        snapshot_id,
        runtime_status,
        verdict,
        request_payload,
        response_payload,
        created_at
    FROM {}.logistics_agent_runs
    """
)


def insert_logistics_agent_run(
    conn: Any,
    *,
    cycle: LogisticsCycle,
    as_of: date,
    snapshot_id: str | None,
    runtime_status: RuntimeStatus,
    verdict: FinalVerdict | None,
    request_payload: dict[str, object],
    response_payload: dict[str, object],
) -> dict[str, Any]:
    """실행이력 한 줄 INSERT … RETURNING. 줄이 안 오면 멈춘다(종전 `execute_returning_one` 문구)."""
    query = sql.SQL(
        """
        INSERT INTO {}.logistics_agent_runs (
            run_id,
            cycle,
            as_of,
            snapshot_id,
            runtime_status,
            verdict,
            request_payload,
            response_payload
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING
            run_id,
            cycle,
            as_of,
            snapshot_id,
            runtime_status,
            verdict,
            request_payload,
            response_payload,
            created_at
        """
    ).format(sql.Identifier(get_db_schema()))
    with conn.cursor() as cursor:
        cursor.execute(
            query,
            (
                uuid4(),
                cycle,
                as_of,
                snapshot_id,
                runtime_status,
                verdict,
                Jsonb(request_payload),
                Jsonb(response_payload),
            ),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Database write did not return a row")
        return row


def select_logistics_agent_run(conn: Any, *, run_id: UUID) -> dict[str, Any] | None:
    """실행이력 한 줄 (`run_id`)."""
    query = _SELECT_COLUMNS.format(sql.Identifier(get_db_schema())) + sql.SQL(" WHERE run_id = %s")
    with conn.cursor() as cursor:
        cursor.execute(query, (run_id,))
        return cursor.fetchone()


def select_logistics_agent_runs(
    conn: Any,
    *,
    cycle: LogisticsCycle | None,
    as_of: date | None,
    runtime_status: RuntimeStatus | None,
    verdict: FinalVerdict | None,
    limit: int,
) -> list[dict[str, Any]]:
    """필터에 맞는 실행이력 — 최신부터 `limit` 건."""
    conditions: list[sql.Composable] = []
    params: list[object] = []
    if cycle is not None:
        conditions.append(sql.SQL("cycle = %s"))
        params.append(cycle)
    if as_of is not None:
        conditions.append(sql.SQL("as_of = %s"))
        params.append(as_of)
    if runtime_status is not None:
        conditions.append(sql.SQL("runtime_status = %s"))
        params.append(runtime_status)
    if verdict is not None:
        conditions.append(sql.SQL("verdict = %s"))
        params.append(verdict)

    query = _SELECT_COLUMNS.format(sql.Identifier(get_db_schema()))
    if conditions:
        query += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)
    query += sql.SQL(" ORDER BY created_at DESC, run_id DESC LIMIT %s")
    params.append(limit)
    with conn.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()
