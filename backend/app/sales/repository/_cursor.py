"""판매 repository 의 실행 도우미 — **받은 연결**의 커서로 실행하고 행을 돌려준다.

★ 2026-09-29 BL-013: 전에는 `sales/db.py` 의 `fetch_one` · `fetch_all` · `execute_returning_one`
  이 호출마다 풀에서 연결을 **스스로** 빌렸다(조회는 조회 연결, 쓰기는 한 호출 = 한
  트랜잭션). 이제 연결과 트랜잭션은 부르는 쪽이 정하고, 여기는 실행만 한다. 실행 모양
  (`cursor.execute(query, params)` · `fetchone` · `fetchall`)은 그 헬퍼들과 같다.
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
    """변경 SQL 의 `RETURNING` 한 행. **행이 안 나오면 예외다** — 문구는 종전
    `execute_returning_one` 과 같다.
    """
    with conn.cursor() as cursor:
        cursor.execute(query, params)
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Database write did not return a row")
        return row
