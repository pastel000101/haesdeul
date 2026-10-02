"""판매 repository 의 실행 도우미 — 받은 연결의 커서로 실행하고 행을 돌려준다.

연결과 트랜잭션은 부르는 쪽이 정하고, 여기는 실행만 한다.
"""

from typing import Any

from psycopg import Connection

from app.core.db import Params, Query


def fetch_one(conn: Connection, query: Query, params: Params = None) -> dict[str, Any] | None:
    """한 행. 없으면 `None` 이다."""
    with conn.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchone()


def fetch_all(conn: Connection, query: Query, params: Params = None) -> list[dict[str, Any]]:
    """모든 행. 없으면 빈 목록이다."""
    with conn.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()


def returning_one(conn: Connection, query: Query, params: Params = None) -> dict[str, Any]:
    """변경 SQL 의 `RETURNING` 한 행. 행이 안 나오면 `RuntimeError` 다."""
    with conn.cursor() as cursor:
        cursor.execute(query, params)
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Database write did not return a row")
        return row
