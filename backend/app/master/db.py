"""마스터의 SQL 실행 입구 — 조회 · 쓰기 헬퍼.

★ 2026-09-29 재구성 BL-014: `app/finance/db.py` 에서 옮겼다. 마스터는 자기 DB 모듈이 없어
  재무 입구의 헬퍼를 빌려 써 왔는데(마스터 20개 파일), 재무가 SQL 을 `finance/repository/`
  로 옮기면서 재무 쪽에는 이 헬퍼를 쓰는 곳이 없어졌다. 헬퍼의 몸통·대여 방식은 그대로다.
  **마스터가 재무 SQL 헬퍼에 기대던 의존을 끊으려고 둔 임시 자리다** — 마스터 계층화(BL-018)에서
  `master/repository/` 로 흡수하고 이 파일은 없앤다.

★ 연결 풀 · 대여 · 반환은 `app/core/db.py`, 스키마 이름의 원천은 `app/core/settings.py` 다. 아래
  함수는 SQL 실행을 눈에 보이게 하는 입구다 (2026-09-29 풀 전환 때 재무 입구에 두었던 모양 그대로).
  마스터 모듈은 지금 스키마 이름(`get_db_schema`)도 이 입구에서 받는다 — 종전 재무 입구에서 받던
  import 모양을 그대로 옮긴 것이다(실행 행을 만드는 마스터 모듈의 원문 잠금
  `tests/master/test_sim_run_axis.py` · `test_sim_run_open.py` 가 `app.core` 글자를 막는다).
  이 재수출은 설계 원칙이 아니라 이행 중의 모양이다. 마스터 repository 가 스키마 이름을 어디서
  받을지와 그 원문 잠금을 어떻게 바꿀지는 BL-018 에서 정한다(2026-09-29 BL-014 후속 정리).

  🔴 연결은 **호출할 때** `app.core.db` 의 대여 함수로 빌린다 — 조회는 조회 연결(autocommit),
     쓰기는 한 호출 = 한 트랜잭션.
"""

from typing import Any

from app.core import db as core_db
from app.core.db import Params, Query
from app.core.settings import get_db_schema

__all__ = ["execute_returning_one", "fetch_all", "fetch_one", "get_db_schema"]


def fetch_one(query: Query, params: Params = None) -> dict[str, Any] | None:
    """Parameter binding을 사용해 단건 SELECT 결과를 반환한다. 풀에서 조회 연결을 빌린다."""
    with core_db.read_connection() as conn, conn.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchone()


def fetch_all(query: Query, params: Params = None) -> list[dict[str, Any]]:
    """Parameter binding을 사용해 다건 SELECT 결과를 반환한다. 풀에서 조회 연결을 빌린다."""
    with core_db.read_connection() as conn, conn.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()


def execute_returning_one(query: Query, params: Params = None) -> dict[str, Any]:
    """변경 SQL을 실행하고 RETURNING으로 생성된 단건 결과를 반환한다.

    한 호출 = 한 트랜잭션(연결을 빌려 commit 하고 돌려준다). RETURNING 행이 없으면
    `RuntimeError` 를 올리고 그 예외로 rollback 된다.
    """
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
