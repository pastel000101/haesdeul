"""판매 운영 콘솔의 실행이력 SQL — 실행 축은 저장된 봉투 안의 값으로 거른다.

행을 응답으로 펴는 것은 `readmodel/console_runs.py` 다.
"""

from datetime import date
from typing import Any

from psycopg import Connection, sql

from app.core.settings import get_db_schema
from app.sales.repository._cursor import fetch_all

#: Deterministic order: newest first with a stable tie-break, so two runs stored in
#: the same second keep one fixed order between identical requests.
_ORDER = sql.SQL(" ORDER BY s.created_at DESC, s.run_id DESC")

#: Where Sales stores the axis inside its own request payload.
_AXIS = sql.SQL("s.request_payload->'context'->>'sim_run_id'")


def load_run_rows(
    conn: Connection,
    *,
    sim_run_id: str,
    as_of: date | None,
    partner_id: str | None,
    item: str | None,
    runtime_status: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Stored Sales run rows belonging to exactly one simulation run."""
    schema = get_db_schema()
    conditions: list[sql.Composable] = [_AXIS + sql.SQL(" = %s")]
    params: list[object] = [sim_run_id]
    if as_of is not None:
        conditions.append(sql.SQL("s.as_of = %s"))
        params.append(as_of)
    if partner_id is not None:
        conditions.append(
            sql.SQL("s.request_payload->'payload'->'user_request'->>'partner_id' = %s")
        )
        params.append(partner_id)
    if item is not None:
        conditions.append(sql.SQL("s.request_payload->'payload'->'user_request'->>'item' = %s"))
        params.append(item)
    if runtime_status is not None:
        conditions.append(sql.SQL("s.runtime_status = %s"))
        params.append(runtime_status)
    statement = (
        sql.SQL(
            """
            SELECT s.run_id, s.as_of, s.runtime_status, s.request_payload,
                   s.response_payload, s.created_at, p.partner_name,
                   m.end_code AS master_end_code
            FROM {schema}.sales_agent_runs s
            LEFT JOIN {schema}.partners p
              ON p.partner_id = s.request_payload->'payload'->'user_request'->>'partner_id'
            LEFT JOIN LATERAL (
                SELECT end_code
                FROM {schema}.master_agent_runs
                WHERE request_id = s.request_payload->'context'->>'request_id'
                  AND sim_run_id = {axis}
                ORDER BY run_seq DESC, created_at DESC
                LIMIT 1
            ) m ON TRUE
            WHERE
            """
        ).format(schema=sql.Identifier(schema), axis=_AXIS)
        + sql.SQL(" AND ").join(conditions)
        + _ORDER
        + sql.SQL(" LIMIT %s")
    )
    params.append(limit)
    return fetch_all(conn, statement, params)
