"""run_repository.py - 마스터 실행이력 표 접근. **마스터 소유다.**

★ 왜 `app/orchestrator/run_repository.py` 를 안 쓰는가 (2026-09-02)
  그 모듈은 오케 · Critic · 마스터가 한 표(`orchestrator_agent_runs`)를 쓰던 시절의
  것이고, `agent` 축으로 셋을 갈랐다. 어휘의 소유가 없어서 마스터가 조회(`STATUS`)를
  이력에 남기려 해도 CHECK 를 못 고쳤다 - 남의 행의 뜻까지 건드리기 때문이다.

  마스터 표를 따로 두면서 이 모듈이 그 표를 소유한다. Critic 은 옛 모듈을 그대로
  쓴다 - 남의 코드를 건드리지 않는다.

★ 계산과 적재를 섞지 않는다.
  `flow.py` 는 DB 를 모르고 `service.py` 는 경계 변환만 한다. 여기서만 SQL 을 쓴다.

★ 적재 실패가 응답을 막지 않는다.
  이력이 없는 것보다 결과를 못 주는 것이 나쁘다 - `try_save_run` 이 삼킨다.
  다만 **읽기는 삼키지 않는다.** 없는 실행을 빈 값으로 돌려주면 화면이
  "실행이 없다" 와 "DB 가 죽었다" 를 구별하지 못한다.

★ 2026-09-30 재구성 BL-018: `master/run_repository.py` 에서 SQL 만 남겼다. 전에는 함수마다
  `master/db.py` 의 헬퍼가 호출마다 연결을 스스로 빌렸다. 이제 여기는 **받은 연결로 실행만**
  하고, 연결은 부르는 쪽이 빌린다 — 조회는 `readmodel/runs.py`(조회 하나에 조회 연결 하나),
  적재는 `service/run_history.py`(연결 하나 · 트랜잭션 하나). 빌리는 횟수와 종류는 종전 헬퍼와
  같다. 업무 키 규칙은 `domain/request_ids.py`, 행 모양은 `schemas/runs.py`, 빈 축 접기와 걷기
  범위 검사는 `domain/runs.py`, 적재 스위치(`history_enabled`)와 실패 삼킴(`try_save_run`)은
  `service/run_history.py` 로 갈랐다.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from psycopg import sql
from psycopg.types.json import Jsonb

from app.master.domain.request_ids import LEDGER_GAP_REQUEST_LIKE
from app.master.domain.runs import null_if_blank

_TABLE = "master_agent_runs"

_COLUMNS = (
    "run_id",
    "request_id",
    "as_of",
    "cycle",
    "run_seq",
    "item",
    "end_code",
    "runtime_status",
    "coverage_ran",
    "coverage_total",
    "elapsed_ms",
    "plan",
    "request_payload",
    "response_payload",
    "sim_run_id",
    "created_at",
)


def _select(schema: str) -> sql.Composed:
    return sql.SQL("SELECT {} FROM {}.{}").format(
        sql.SQL(", ").join(sql.Identifier(c) for c in _COLUMNS),
        sql.Identifier(schema),
        sql.Identifier(_TABLE),
    )


def insert_run(
    conn: Any,
    *,
    schema: str,
    run_id: UUID,
    cycle: str,
    as_of: date,
    request_payload: dict[str, object],
    response_payload: dict[str, object],
    request_id: str | None,
    run_seq: int,
    item: str | None,
    end_code: str | None,
    runtime_status: str,
    coverage_ran: int | None,
    coverage_total: int | None,
    elapsed_ms: int | None,
    plan: list[dict[str, object]] | None,
    sim_run_id: str | None,
) -> dict[str, Any]:
    """실행 1건 INSERT … RETURNING. **행이 안 나오면 예외다** — 문구는 종전 헬퍼와 같다.

    🔴 빈 축은 NULL 로 접는다 (`null_if_blank`). 기본값 `""` 는 *"아직 안 실렸다"* 이지 값이 아니다.
    """
    query = sql.SQL(
        """
        INSERT INTO {}.{} (
            run_id, request_id, as_of, cycle, run_seq,
            item, end_code, runtime_status,
            coverage_ran, coverage_total, elapsed_ms,
            plan, request_payload, response_payload,
            sim_run_id
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s
        )
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
                request_id,
                as_of,
                cycle,
                run_seq,
                item,
                end_code,
                runtime_status,
                coverage_ran,
                coverage_total,
                elapsed_ms,
                None if plan is None else Jsonb(plan),
                Jsonb(request_payload),
                Jsonb(response_payload),
                null_if_blank(sim_run_id),
            ),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Database write did not return a row")
        return row


def select_run(conn: Any, run_id: UUID, *, schema: str) -> dict[str, Any] | None:
    """UUID 로 실행 1건. 없으면 `None`."""
    query = _select(schema) + sql.SQL(" WHERE run_id = %s")
    with conn.cursor() as cursor:
        cursor.execute(query, (run_id,))
        return cursor.fetchone()


def select_latest_run(
    conn: Any, request_id: str, *, cycle: str | None, schema: str
) -> dict[str, Any] | None:
    """업무 키로 가장 최근 실행 1건(`cycle` 을 주면 그 사이클만). 없으면 `None`."""
    clauses = [sql.SQL("request_id = %s")]
    params: list[Any] = [request_id]
    if cycle is not None:
        clauses.append(sql.SQL("cycle = %s"))
        params.append(cycle)

    query = (
        _select(schema)
        + sql.SQL(" WHERE ")
        + sql.SQL(" AND ").join(clauses)
        + sql.SQL(" ORDER BY created_at DESC, run_seq DESC LIMIT 1")
    )
    with conn.cursor() as cursor:
        cursor.execute(query, tuple(params))
        return cursor.fetchone()


def select_runs(
    conn: Any,
    *,
    request_id: str | None,
    as_of: date | None,
    as_of_before: date | None,
    cycle: str | None,
    item: str | None,
    sim_run_id: str | None,
    limit: int,
    schema: str,
) -> list[dict[str, Any]]:
    """주어진 조건만 AND 로 붙인 실행 목록. 최신부터 `limit` 건."""
    clauses: list[sql.Composable] = []
    params: list[Any] = []
    for column, value in (
        ("request_id", request_id),
        ("as_of", as_of),
        ("cycle", cycle),
        ("item", item),
        ("sim_run_id", sim_run_id),
    ):
        if value is not None:
            clauses.append(sql.SQL("{} = %s").format(sql.Identifier(column)))
            params.append(value)
    if as_of_before is not None:
        clauses.append(sql.SQL("{} < %s").format(sql.Identifier("as_of")))
        params.append(as_of_before)

    query = _select(schema)
    if clauses:
        query = query + sql.SQL(" WHERE ") + sql.SQL(" AND ").join(clauses)
    query = query + sql.SQL(" ORDER BY created_at DESC LIMIT %s")
    params.append(limit)

    with conn.cursor() as cursor:
        cursor.execute(query, tuple(params))
        return cursor.fetchall()


def select_run_counts_by_day(
    conn: Any, *, sim_run_id: str, start: date, end: date, schema: str
) -> list[dict[str, Any]]:
    """한 걷기의 날짜별 집계 행. 세는 것은 이 SQL 이 다 한다 — 파이썬에서 세지 않는다."""
    query = sql.SQL(
        """
        WITH filtered AS (
            SELECT as_of, end_code, item, request_id
            FROM {schema}.{table}
            WHERE sim_run_id = %s AND as_of >= %s AND as_of <= %s
        ),
        per_code AS (
            SELECT as_of, end_code, COUNT(*)::int AS n
            FROM filtered
            WHERE end_code IS NOT NULL
            GROUP BY as_of, end_code
        ),
        per_day AS (
            SELECT
                as_of,
                COUNT(*)::int AS runs,
                COALESCE(
                    ARRAY_AGG(DISTINCT item) FILTER (WHERE item IS NOT NULL),
                    ARRAY[]::text[]
                ) AS items,
                -- 🔴 **업무 키로 관문 행을 알아본다** (2026-09-09). 옛 판정은
                --    품목이 비고 종료코드가 `E4_NOT_STARTED` 인 **모양**이었는데,
                --    그 모양은 관문 행만의 것이 아니다 — 품목을 정하기 전에 죽은
                --    옛 매입 실행 14행이 실측으로 같은 모양이다. 축이 막고 있었을
                --    뿐이고, 축이 실린 채로 하나만 나오면 그날이 *"관문이 막았다"*
                --    로 잘못 읽힌다.
                --
                -- ★ 파이썬 쪽 판정(`is_ledger_gap_request_id`)과 **같은 꼬리**를
                --   본다. 패턴은 `LEDGER_GAP_REQUEST_LIKE` 가 만든다 — 날짜 형식은
                --   여기 없다.
                BOOL_OR(request_id LIKE %s) AS gate_blocked
            FROM filtered
            GROUP BY as_of
        )
        SELECT
            d.as_of,
            d.runs,
            d.items,
            d.gate_blocked,
            COALESCE(
                (
                    SELECT jsonb_object_agg(p.end_code, p.n)
                    FROM per_code p
                    WHERE p.as_of = d.as_of
                ),
                '{{}}'::jsonb
            ) AS end_codes
        FROM per_day d
        ORDER BY d.as_of
        """
    ).format(
        schema=sql.Identifier(schema),
        table=sql.Identifier(_TABLE),
    )
    with conn.cursor() as cursor:
        cursor.execute(query, (sim_run_id, start, end, LEDGER_GAP_REQUEST_LIKE))
        return cursor.fetchall()
