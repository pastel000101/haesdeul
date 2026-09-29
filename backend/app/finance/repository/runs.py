"""재무 에이전트 실행이력 SQL — v1(`finance_agent_runs`) · v2.2(`finance_agent_runs_v22`).

★ 2026-09-29 재구성 BL-014: `finance/execution.py` 에서 옮겼다. 받은 연결로 실행만 한다.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID, uuid4

from psycopg import sql
from psycopg.types.json import Jsonb

from app.contracts.envelope import AgentReply, AgentRequest, ExecutionMetadata
from app.core.settings import get_db_schema
from app.finance.repository._cursor import fetch_all, fetch_one, returning_one
from app.finance.schemas.agent import FinalVerdict, FinanceCycle, RuntimeStatus

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
    FROM {}.finance_agent_runs
    """
)


def insert_finance_agent_run(
    conn: Any,
    *,
    cycle: FinanceCycle,
    as_of: date,
    snapshot_id: str | None,
    runtime_status: RuntimeStatus,
    verdict: FinalVerdict | None,
    request_payload: dict[str, object],
    response_payload: dict[str, object],
) -> dict[str, Any]:
    """v1 실행이력 한 행을 적고 `RETURNING` 행을 돌려준다."""
    query = sql.SQL(
        """
        INSERT INTO {}.finance_agent_runs (
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
    return returning_one(
        conn,
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


def select_finance_agent_run(conn: Any, run_id: UUID) -> dict[str, Any] | None:
    """v1 실행이력 한 행. 없으면 `None`."""
    query = _SELECT_COLUMNS.format(sql.Identifier(get_db_schema())) + sql.SQL(" WHERE run_id = %s")
    return fetch_one(conn, query, (run_id,))


def select_finance_agent_runs(
    conn: Any,
    *,
    cycle: FinanceCycle | None,
    as_of: date | None,
    runtime_status: RuntimeStatus | None,
    verdict: FinalVerdict | None,
    limit: int,
) -> list[dict[str, Any]]:
    """필터에 맞는 v1 실행이력 — 최신부터."""
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
    return fetch_all(conn, query, params)


def insert_finance_execution(
    conn: Any, *, request: AgentRequest, reply: AgentReply, metadata: ExecutionMetadata
) -> dict[str, Any]:
    """v2.2 실행이력 한 행을 적는다. `RETURNING run_id` 행이 없으면 예외다."""
    query = sql.SQL(
        """
        INSERT INTO {}.finance_agent_runs_v22 (
            run_id, request_id, agent, mode, as_of, policy_version, trigger, call_seq,
            runtime_status,
            business_status, request_payload, response_payload,
            used_tools, tool_order, observations, rules_applied, replans,
            llm_status, llm_model, llm_attempts, llm_fallback_used, elapsed_ms
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        """
    ).format(sql.Identifier(get_db_schema()))
    return returning_one(
        conn,
        query + sql.SQL(" RETURNING run_id"),
        (
            UUID(reply.run_id),
            request.context.request_id,
            "finance",
            request.mode,
            request.context.as_of,
            request.context.policy_version,
            request.context.trigger,
            request.call_seq,
            reply.runtime_status,
            reply.business_status,
            Jsonb(dict(request.payload)),
            Jsonb(dict(reply.payload)),
            Jsonb(list(metadata.used_tools)),
            Jsonb(list(metadata.tool_order)),
            Jsonb(list(metadata.observations)),
            Jsonb(list(metadata.rules_applied)),
            metadata.replans,
            metadata.llm_status,
            metadata.llm_model,
            metadata.llm_attempts,
            metadata.llm_fallback_used,
            metadata.elapsed_ms,
        ),
    )


def select_finance_execution(conn: Any, run_id: UUID) -> dict[str, Any] | None:
    """v2.2 실행이력 한 행. 없으면 `None`."""
    query = sql.SQL("SELECT * FROM {}.finance_agent_runs_v22 WHERE run_id = %s").format(
        sql.Identifier(get_db_schema())
    )
    return fetch_one(conn, query, (run_id,))
