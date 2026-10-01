"""영업 Agent 실행이력 전용 PostgreSQL Repository — `sales_agent_runs`.

★ 2026-09-29 BL-013: `sales/runs.py` 에서 SQL 과 payload 직렬화를 옮기고 연결을 인자로 받게
  했다. 행을 응답 모델로 펴는 조회는 `readmodel/runs.py`, 저장 트랜잭션은
  `service/proposal_generation.py` 가 연다(한 저장 = 한 트랜잭션, 종전과 같다).
"""

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from psycopg import Connection, sql
from psycopg.types.json import Jsonb

from app.core.settings import get_db_schema
from app.sales.repository._cursor import fetch_all, fetch_one, returning_one
from app.sales.schemas.runs import RuntimeStatus, SalesAgentRun, SalesCycle


def _json_safe(value: Any) -> str:
    """이력 payload 에 실을 수 없는 값을 **아는 것만** 편다.

    🔴 **`date` 가 그대로 실려 이력 저장이 터지고 있었다** (2026-09-11 실측 · 걷기
       `SIM-WALK-2026-FULL` 206일에서 **123건**). `asdict(request)` 는 `date` 를
       그대로 두는데 `Jsonb` 는 그것을 못 싣는다.

    ★ 저장소 관례가 이미 `model_dump(mode="json")` 이다 (`app/logistics/service/cycle.py` ·
      `app/master/service/cycle_persistence.py`). 둘 다 날짜를 ISO 문자열로 편다. 여기만
      `dataclasses.asdict` 라 안 펴졌다 — **같은 결로 맞춘다.**

    🔴 **모르는 것은 `str()` 로 뭉개지 않는다.** `default=str` 로 통째로 접으면
       앞으로 실리는 어떤 타입이든 조용히 문자열이 되고, 그 손실이 이력에만 남아
       아무도 안 아프다. 아는 셋만 펴고 나머지는 **터뜨린다.**
    """
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    raise TypeError(f"이력 payload 에 실을 수 없는 값이다: {type(value).__name__}")


def _payload(value: object) -> Jsonb:
    """이력 payload 하나. **`Jsonb` 를 만드는 자리는 여기 하나다.**"""
    return Jsonb(value, dumps=lambda obj: json.dumps(obj, default=_json_safe))


_SELECT_COLUMNS = sql.SQL(
    """
    SELECT
        run_id,
        cycle,
        as_of,
        snapshot_id,
        runtime_status,
        request_payload,
        response_payload,
        created_at
    FROM {}.sales_agent_runs
    """
)


def save_sales_agent_run(
    conn: Connection,
    *,
    run_id: UUID | None = None,
    cycle: SalesCycle,
    as_of: date,
    snapshot_id: str | None,
    runtime_status: RuntimeStatus,
    request_payload: dict[str, object],
    response_payload: dict[str, object],
) -> SalesAgentRun:
    """완성된 영업 Agent Request와 Response를 실행이력으로 저장한다."""
    query = sql.SQL(
        """
        INSERT INTO {}.sales_agent_runs (
            run_id,
            cycle,
            as_of,
            snapshot_id,
            runtime_status,
            request_payload,
            response_payload
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING
            run_id,
            cycle,
            as_of,
            snapshot_id,
            runtime_status,
            request_payload,
            response_payload,
            created_at
        """
    ).format(sql.Identifier(get_db_schema()))
    row = returning_one(
        conn,
        query,
        (
            run_id or uuid4(),
            cycle,
            as_of,
            snapshot_id,
            runtime_status,
            _payload(request_payload),
            _payload(response_payload),
        ),
    )
    return cast(SalesAgentRun, row)


def get_sales_agent_run(conn: Connection, run_id: UUID) -> SalesAgentRun:
    """run_id로 영업 Agent 실행이력 한 건을 조회한다."""
    query = _SELECT_COLUMNS.format(sql.Identifier(get_db_schema())) + sql.SQL(" WHERE run_id = %s")
    row = fetch_one(conn, query, (run_id,))
    if row is None:
        raise LookupError(f"Sales Agent run was not found: {run_id}")
    return cast(SalesAgentRun, row)


def list_sales_agent_runs(
    conn: Connection,
    *,
    cycle: SalesCycle | None = None,
    as_of: date | None = None,
    snapshot_id: str | None = None,
    runtime_status: RuntimeStatus | None = None,
    limit: int = 100,
) -> list[SalesAgentRun]:
    """선택한 필터로 최신 영업 Agent 실행이력을 조회한다."""
    conditions: list[sql.Composable] = []
    params: list[object] = []
    if cycle is not None:
        conditions.append(sql.SQL("cycle = %s"))
        params.append(cycle)
    if as_of is not None:
        conditions.append(sql.SQL("as_of = %s"))
        params.append(as_of)
    if snapshot_id is not None:
        conditions.append(sql.SQL("snapshot_id = %s"))
        params.append(snapshot_id)
    if runtime_status is not None:
        conditions.append(sql.SQL("runtime_status = %s"))
        params.append(runtime_status)

    query = _SELECT_COLUMNS.format(sql.Identifier(get_db_schema()))
    if conditions:
        query += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)
    query += sql.SQL(" ORDER BY created_at DESC, run_id DESC LIMIT %s")
    params.append(limit)
    return cast(list[SalesAgentRun], fetch_all(conn, query, params))
