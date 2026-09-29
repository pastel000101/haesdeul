"""영업 Agent의 PostgreSQL 접근 기능.

기존 재고·물류 Agent와 같은 환경변수 기반 연결을 사용한다.

★ 연결 풀 · 대여 · 반환은 `app/core/db.py`, 설정은 `app/core/settings.py` 다 (2026-09-29 풀 전환).
  이 파일은 **영업 입구**다 — 이름은 그대로 두고, SQL 실행은 여기서 눈에 보이게 한다.
  연결은 호출할 때 `app.core.db` 의 대여 함수로 빌린다.

★ 종전 영업 읽기 범위(`read_connection_scope` · 2026-09-17)는 없앴다. 그 범위가 막던
  «조회마다 새 연결» 을 이제 풀이 막는다.
"""

from typing import Any

from app.core import db as core_db
from app.core import settings
from app.core.db import Params, Query


def get_db_schema() -> str:
    """설정된 PostgreSQL Schema 이름을 반환한다."""
    return settings.get_db_schema()


def fetch_one(query: Query, params: Params = None) -> dict[str, Any] | None:
    """Parameter binding을 사용해 단건 조회 결과를 반환한다. 풀에서 조회 연결을 빌린다."""
    with core_db.read_connection() as conn, conn.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchone()


def fetch_all(query: Query, params: Params = None) -> list[dict[str, Any]]:
    """Parameter binding을 사용해 다건 조회 결과를 반환한다. 풀에서 조회 연결을 빌린다."""
    with core_db.read_connection() as conn, conn.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()


def execute_returning_one(query: Query, params: Params = None) -> dict[str, Any]:
    """변경 SQL을 실행하고 RETURNING 단건 결과를 반환한다. 한 호출 = 한 트랜잭션."""
    with (
        core_db.connection() as conn,
        core_db.transaction(conn),
        conn.cursor() as cursor,
    ):
        cursor.execute(query, params)
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Database write did not return a row")
        return row
