"""승인 전이 SQL — 그날 fixture 행 잠금 · 입고 status 두 칸 CONFIRMED.

부르는 쪽은 `service/transition.py` 의 `persist_inventory` 다(잠금 → 갱신 순서).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from psycopg import sql

from app.logistics.repository.rows import get_db_schema
from app.logistics.schemas.vocabulary import USAGE_SCOPE


def lock_fixture_row(conn: Any, *, sim_run_id: str, as_of: date) -> bool:
    """그날 그 실행의 fixture 행을 `FOR UPDATE` 로 잠근다. 행이 있으면 참.

    세 조건이 `confirm_fixture_statuses` 의 WHERE 와 같아야 한다. 다르면 잠근 행과
    갱신한 행이 갈려 잠금이 그 갱신을 지키지 못한다.

    `FOR UPDATE` 가 승인 전이의 동시성 방어다. 같은 fixture 행을 겨냥한 승인은 이 행
    잠금으로 직렬화되고, 잠금은 바깥 트랜잭션이 커밋/롤백할 때까지 유지된다(잠금 순서는
    `service/transition.py` 의 `persist_inventory` docstring).

    행이 있는지 보고 잠그기만 한다. 목록은 여기서 읽지 않는다 — 업무 사실의 정본이
    `inbound_schedules` 다(W3-3).
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

    두 JSON 칸은 쓰지 않는다(W3-3). 쓰는 것은 Header 표시 둘뿐이다 — `*_status` 는
    Reader 가 «그 축을 확인했나» 를 가르는 데 쓴다(`domain/snapshot.schedule_source`:
    `UNRESOLVED` 면 목록을 숨긴다). 갱신하지 않으면 `UNRESOLVED` 인 날에 승인이 나도
    그 일정이 영영 안 보인다.
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
