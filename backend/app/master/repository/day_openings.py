"""
day_opening_repository.py — 개장 정본(`master_day_openings`) 적재·조회.

🔴 **파트 트랜잭션 밖에서 쓴다.**

  실패도 기록해야 시도 횟수를 셀 수 있는데, 파트 트랜잭션 안에 넣으면 **롤백될 때
  실패했다는 사실까지 사라진다.** `persistence.record` 가 응답 이력을 따로 남기는 것과
  같은 자리다.

🔴 **적재 실패가 개장을 죽이지 않는다.**

  이력이 없는 것보다 하루를 못 여는 것이 나쁘다. `run_repository.try_save_run` 이
  *"이력이 없는 것보다 결과를 못 주는 것이 나쁘다"* 라 적은 것과 같은 판단이고,
  **조용히 넘어가지는 않는다** — 로그에 남긴다.

★ **정본 키는 `(as_of, sim_run_id)` 다** (재무·물류 2026-09-06 합의).

  ```text
  Master 공통 정본     (as_of, sim_run_id)
  Finance 실제 상태     (sim_run_id, as_of, financing_mode)
  Logistics 실제 상태   (sim_run_id, as_of, usage_scope)
  ```

  ⚠️ `financing_mode` · `usage_scope` 는 **파트 고유 축**이라 마스터가 안 가진다.
    가지기 시작하면 파트가 늘 때마다 정본 키가 바뀐다.

🔴 **두 칸을 센다. 계약이 하나만 적은 것이 얕았다.**

  ```text
  attempt_count   이 날에 몇 번 불렀나 (성공·실패 다)
  failure_count   **연속** 실패 — 성공하면 0 으로 돌아간다
  ```

  ★ `next_action` 이 쓰는 것은 `failure_count` 다. 계약이 *"실패 1회째는 재시도,
    2회 이상은 사람"* 이라 했는데, **어제 성공하고 오늘 처음 실패한 것을 "2번째" 로
    세면 재시도 한 번 없이 사람을 부른다.**

★ 2026-09-30 재구성 BL-018: `master/day_opening_repository.py` 에서 SQL 만 남겼다 — 받은 연결로
  실행한다
  (`upsert_day_opening` · `select_day_opening` · `select_opened_days_after`, 판매 조회에 끼우는 조각
  `handled_on_first_open_day`). 연결을 빌리고 commit · rollback 하고 실패를 삼키는 것은 종전 그대로
  `service/day_open.record_day_opening`(적재) · `readmodel/day_openings.py`(조회)가 한다. 기록
  모양은
  `schemas/day_open.DayOpeningRecord`.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

from app.core.settings import get_db_schema

TABLE = "master_day_openings"

#: 조회 컬럼 순서. `read_day_opening` 의 SELECT 와 **같아야 한다.**
COLUMNS = ("as_of", "sim_run_id", "result", "attempt_count", "failure_count", "reason")

#: 성공 어휘. 이 둘이면 연속 실패가 0 으로 돌아간다.
SUCCESS = frozenset({"OPENED", "ALREADY_OPENED"})


def table(schema: str | None = None) -> sql.Composable:
    """개장 정본 표. `schema` 를 안 주면 여기서 읽는다(연결을 받아 쓰는 SQL 조각 — 종전과 같다)."""
    name = get_db_schema() if schema is None else schema
    return sql.SQL("{}.{}").format(sql.Identifier(name), sql.Identifier(TABLE))


def handled_on_first_open_day(
    *, sale_date: sql.Composable, sim_run_id: sql.Composable
) -> sql.Composable:
    """「그 판매의 납품 처리일 = 납품일 당일, 그날이 개장일이 아니면 그 뒤 첫 개장일」.

    `WHERE` 에 끼우는 조건 한 덩이다. **`as_of` 를 세 번 받는다** (`%s` 셋).

    ```text
    sale_date = as_of                                        당일
    sale_date < as_of  AND  [sale_date, as_of) 에 개장 행 0   납품일 뒤 첫 개장일
    ```

    🔴 **휴장일에 걷기가 그날을 건너뛴다** (실측 SIM-CHAIN-CHECK-0915).
      금요일 확정 · 토요일 납품(2026-03-07 · 04-04) 판매가 정확 일치에 걸려 다음
      개장일에도 안 잡혔다 — 예약 6건이 할당 0 으로 재고를 잡고, 채권도 안 섰다.

    🔴 **backorder 가 아니다.** 한 번 처리된 날(출고 · SHORT · 놓아줌) 뒤에는 그
      사이에 개장 행이 서므로 **다음 날 다시 안 잡힌다.** `sale_date <= as_of AND
      미출고` 로 넓히면 SHORT · 놓아준 예약을 매일 다시 집는다 — 쓰지 않는다.

    ★ **성공한 개장만 센다** (`opened_days_after` 와 같은 표). `as_of` 자신의 행은
      `< as_of` 라 안 센다 — 오늘 행이 먼저 섰든 나중에 서든 답이 같다.

    ⚠️ 이 표가 생기기 전의 날은 모른다 (`opened_days_after` 참조).
    """
    return sql.SQL(
        "({sale_date} = %s OR ({sale_date} < %s AND NOT EXISTS ("
        "SELECT 1 FROM {table} AS o"
        " WHERE o.sim_run_id = {sim_run_id}"
        " AND o.as_of >= {sale_date} AND o.as_of < %s"
        " AND (o.result = 'OPENED' OR o.result = 'ALREADY_OPENED'))))"
    ).format(sale_date=sale_date, sim_run_id=sim_run_id, table=table())


def upsert_day_opening(
    conn: Any,
    *,
    as_of: date,
    sim_run_id: str,
    result: str,
    reason: str,
    parts_payload: list[Any],
    schema: str,
) -> None:
    """그날 개장 결과 한 줄을 넣거나 고친다 — 성공이면 실패 수를 0 으로, 실패면 +1.

    ★ 받은 연결로 실행만 한다. commit · rollback 은 부르는
      쪽(`service/day_open.record_day_opening`)이다.
    """
    succeeded = result in SUCCESS
    query = sql.SQL(
        """
        INSERT INTO {} (as_of, sim_run_id, result, attempt_count, failure_count, reason, parts_json)
        VALUES (%s, %s, %s, 1, %s, %s, %s)
        ON CONFLICT (as_of, sim_run_id) DO UPDATE SET
            result = EXCLUDED.result,
            attempt_count = {}.attempt_count + 1,
            -- 🔴 성공이면 0, 실패면 직전 값 + 1. 여기가 next_action 의 근거다.
            failure_count = CASE WHEN %s THEN 0 ELSE {}.failure_count + 1 END,
            reason = EXCLUDED.reason,
            parts_json = EXCLUDED.parts_json,
            last_attempt_at = now()
        """
    ).format(table(schema), table(schema), table(schema))
    with conn.cursor() as cursor:
        cursor.execute(
            query,
            (
                as_of,
                sim_run_id,
                result,
                0 if succeeded else 1,
                reason or None,
                Jsonb(parts_payload),
                succeeded,
            ),
        )


def select_day_opening(conn: Any, *, as_of: date, sim_run_id: str, schema: str) -> Any:
    """그날 개장 정본 한 행(칸 순서는 `COLUMNS`). 없으면 `None`."""
    query = sql.SQL(
        "SELECT as_of, sim_run_id, result, attempt_count, failure_count, reason"
        " FROM {} WHERE as_of = %s AND sim_run_id = %s"
    ).format(table(schema))
    with conn.cursor() as cursor:
        cursor.execute(query, (as_of, sim_run_id))
        return cursor.fetchone()


def select_opened_days_after(conn: Any, *, after: date, sim_run_id: str, schema: str) -> list[Any]:
    """`after` 다음으로 열린(`OPENED` · `ALREADY_OPENED`) 날 행들. 날짜순."""
    query = sql.SQL(
        "SELECT as_of FROM {} WHERE sim_run_id = %s AND as_of > %s"
        " AND result IN ('OPENED', 'ALREADY_OPENED') ORDER BY as_of"
    ).format(table(schema))
    with conn.cursor() as cur:
        cur.execute(query, (sim_run_id, after))
        return cur.fetchall()
