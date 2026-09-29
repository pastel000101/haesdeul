"""승인 전이 SQL — 그날 fixture 행 잠금 · 입고 status 두 칸 CONFIRMED.

★ 2026-09-30 재구성 BL-015: `logistics/transition.py` 의 `persist_inventory` 안 두 문장을 함수로
  뗐다(문면 · 순서 그대로).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from psycopg import sql

from app.logistics.repository.rows import get_db_schema
from app.logistics.schemas.vocabulary import USAGE_SCOPE


def lock_fixture_row(conn: Any, *, sim_run_id: str, as_of: date) -> bool:
    """그날 그 실행의 fixture 행을 `FOR UPDATE` 로 잠근다. 행이 있으면 참.

    ★ 세 조건이 `confirm_fixture_statuses` 의 WHERE 와 **같아야 한다.** 다르면 읽은 행과
      쓴 행이 갈려 남의 목록에 이번 승인분을 얹게 된다.

    🔴 **`FOR UPDATE` 가 승인 전이의 동시성 방어 전부다.** 없으면 같은 fixture 행을
       겨냥한 두 승인이 **같은 옛 목록을 읽고** 각자 병합해, 나중에 커밋한 쪽이
       앞엣것을 통째로 덮는다 (`persist_inventory` docstring 의 lost-update 표).
       병합을 파이썬에서 하는 이상 읽기와 쓰기 사이가 비어 있고, 그 틈을 닫는 것은
       행 잠금뿐이다.
    ★ **행이 있는지 보고 잠그기만 한다.** 목록은 더 이상 여기서 안 읽는다 —
      업무 사실의 정본이 `inbound_schedules` 다 (W3-3).
    """
    schema = sql.Identifier(get_db_schema())
    select_query = sql.SQL(
        """
        SELECT fixture_id
        FROM {}.logistics_runtime_fixture
        WHERE sim_run_id = %s
          AND as_of = %s
          AND usage_scope = %s
        FOR UPDATE
        """
    ).format(schema)
    with conn.cursor() as cursor:
        cursor.execute(select_query, (sim_run_id, as_of, USAGE_SCOPE))
        return cursor.fetchone() is not None


def confirm_fixture_statuses(conn: Any, *, sim_run_id: str, as_of: date, source_ref: str) -> int:
    """그날 fixture 행의 입고 status 두 칸을 `CONFIRMED` 로, `source_ref` 를 이번 승인으로.

    갱신 행 수.

    🔴 **두 JSON 칸을 안 쓴다 (W3-3).** 남는 것은 Header 표시 둘뿐이다 —
       `*_status` 는 Reader 가 «그 축을 확인했나» 를 가르는 데 여전히 쓴다
       (`domain/snapshot.schedule_source`: `UNRESOLVED` 면 목록을 숨긴다).
       안 갱신하면 `UNRESOLVED` 인 날에 승인이 나도 그 일정이 영영 안 보인다.
    """
    schema = sql.Identifier(get_db_schema())
    query = sql.SQL(
        """
        UPDATE {}.logistics_runtime_fixture
        SET in_transit_status = %s,
            confirmed_inbound_status = %s,
            source_ref = %s,
            updated_at = NOW()
        WHERE sim_run_id = %s
          AND as_of = %s
          AND usage_scope = %s
        """
    ).format(schema)
    with conn.cursor() as cursor:
        cursor.execute(
            query,
            ("CONFIRMED", "CONFIRMED", source_ref, sim_run_id, as_of, USAGE_SCOPE),
        )
        return cursor.rowcount
