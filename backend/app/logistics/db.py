"""Logistics Agent의 PostgreSQL 접근 기능.

★ 연결 풀 · 대여 · 반환은 `app/core/db.py`, 설정은 `app/core/settings.py` 다 (2026-09-29 풀 전환).
  이 파일은 **물류 입구**이고, 다른 부서와 다른 한 가지를 그대로 든다.

  - `.env` 를 프로세스에서 **한 번만** 읽는다 (아래 `_load_env_file_once`). 스키마 이름을
    읽을 때 쓴다. 접속 정보는 풀이 열릴 때 한 번 읽으므로 연결마다 `.env` 를 읽는 일은 없다.
  - `fetch_one`/`fetch_all` 은 호출마다 풀에서 조회 연결을 빌린다. 다른 부서 호출 안에서
    불려도 그쪽 연결을 빌려 쓰지 않는다(종전 «읽기 범위 없음» 과 같은 뜻).
"""

from typing import Any

from dotenv import load_dotenv

from app.core import db as core_db
from app.core.db import Params, Query
from app.core.settings import ENV_FILE, required_database_environment

_ENV_FILE = ENV_FILE

#: 접속이 안 되면 이만큼 기다리고 포기한다 (초). libpq 기본은 0 = 무제한이다.
#: 재무·매입·영업·ML 과 같은 값으로 맞춘다. — 2026-09-28 부터 값의 자리는
#: `app/core/db.py::CONNECT_TIMEOUT_SECONDS` 하나이고, 이 이름은 그 값을 가리킨다.
CONNECT_TIMEOUT_SECONDS = core_db.CONNECT_TIMEOUT_SECONDS

#: ★ `.env` 는 프로세스에서 한 번만 읽는다.
#: 🔴 대시보드 한 요청에 `_required_environment` 가 550회 불리고,
#:    매번 파일을 다시 파싱하느라 7.8초를 썼다.
#: 값은 `required_database_environment` 가 `os.getenv` 로 매번 읽으므로 검사가 환경변수를
#: 바꿔도 그대로 따라간다.
_env_file_loaded = False


def _load_env_file_once() -> None:
    global _env_file_loaded
    if _env_file_loaded:
        return
    load_dotenv(_ENV_FILE)
    _env_file_loaded = True


def _required_environment(keys: tuple[str, ...]) -> dict[str, str]:
    return required_database_environment(keys, load=_load_env_file_once)


def get_db_schema() -> str:
    """설정된 PostgreSQL Schema 이름을 반환한다."""
    return _required_environment(("DB_SCHEMA",))["DB_SCHEMA"]


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
