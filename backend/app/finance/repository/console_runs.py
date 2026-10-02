"""운영 콘솔 재무 실행이력 SQL."""

from datetime import date
from typing import Any

from psycopg import sql

from app.core.settings import get_db_schema
from app.finance.repository._cursor import fetch_all

#: Deterministic order: newest first, then a stable tie-break so two runs written in
#: the same transaction never swap places between two identical requests.
_ORDER = sql.SQL(" ORDER BY f.created_at DESC, f.run_id DESC")


def select_console_finance_runs(
    conn: Any,
    *,
    sim_run_id: str,
    as_of: date | None,
    from_date: date | None,
    to_date: date | None,
    runtime_status: str | None,
    verdict: str | None,
    limit: int,
) -> list[dict[str, object]]:
    schema = get_db_schema()
    conditions: list[sql.Composable] = [
        sql.SQL(
            """EXISTS (SELECT 1 FROM {}.master_agent_runs m
                       WHERE m.request_id = f.request_id AND m.sim_run_id = %s)"""
        ).format(sql.Identifier(schema))
    ]
    params: list[object] = [sim_run_id]
    if as_of is not None:
        conditions.append(sql.SQL("f.as_of = %s"))
        params.append(as_of)
    if from_date is not None:
        conditions.append(sql.SQL("f.as_of >= %s"))
        params.append(from_date)
    if to_date is not None:
        conditions.append(sql.SQL("f.as_of <= %s"))
        params.append(to_date)
    if runtime_status is not None:
        conditions.append(sql.SQL("f.runtime_status = %s"))
        params.append(runtime_status)
    if verdict is not None:
        conditions.append(sql.SQL("f.response_payload->>'finance_verdict' = %s"))
        params.append(verdict)
    query = (
        sql.SQL(
            """
            SELECT f.run_id, f.request_id, f.as_of, f.mode, f.runtime_status,
                   f.business_status, f.llm_status, f.response_payload, f.created_at
            FROM {}.finance_agent_runs_v22 f
            WHERE
            """
        ).format(sql.Identifier(schema))
        + sql.SQL(" AND ").join(conditions)
        + _ORDER
        + sql.SQL(" LIMIT %s")
    )
    params.append(limit)
    return fetch_all(conn, query, params)
