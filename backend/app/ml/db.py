"""ML 파트의 PostgreSQL 접근 기능.

## 왜 연결이 두 개인가

ML 파이프라인은 **창고를 두 개** 쓴다.

    원본 창고 (SOURCE)  경락가·중도매가·소매가 원자료와 학습 테이블이 있는 곳
    서비스 창고 (기본)   다른 Agent 와 같은 곳. 예측 결과를 여기에 넣는다

두 창고는 **같은 서버의 다른 데이터베이스**다. 원본 창고에는 원자료가
수백만 행 쌓여 있어 서비스 창고와 섞지 않는다.

서비스 창고 연결은 다른 모듈과 동일하게 ``DB_*`` 를 쓴다(공통 서비스 풀).
원본 창고 연결만 ``ML_SOURCE_DB_*`` 를 본다(``source_database_settings`` · ``SOURCE_POOL``).
없으면 기본값을 물려받되 데이터베이스 이름만 바꾼다.

★ 연결 풀 · 대여 · 반환은 ``app/core/db.py``, 설정은 ``app/core/settings.py`` 다
  (2026-09-29 풀 전환). 이 파일은 **ML 입구**이고, 원본 창고 접속 정보(``ML_SOURCE_DB_*``)와
  **원본 창고 풀**(``SOURCE_POOL``), ``execute_many`` 는 ML 것으로 남는다. SQL 실행은 여기서
  눈에 보이게 한다.

★ **창고가 둘이라 풀도 둘이다.** 서비스 창고는 다른 부서와 같은 ``core_db.SERVICE_POOL`` 을
  쓰고, 원본 창고는 ``SOURCE_POOL`` 을 쓴다. 섞으면 서비스 질의가 원본 창고로 가서 «표가
  없다» 가 된다. 원본 풀은 처음 원본을 읽을 때 열리고, 앱 · CLI 가 끝날 때 함께 닫힌다.
"""

import os
from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import replace
from typing import Any

from app.core import db as core_db
from app.core import settings
from app.core.db import Connection, Params, Query

#: 접속이 안 되면 이만큼 기다리고 포기한다 (초). 매입 파트 #81 · #358.
#:
#:   libpq 기본은 0 = **무제한**이다. DB 가 "안 됩니다" 라고 거절하면 즉시
#:   오류가 나지만, **아무 답도 안 하면** 계속 기다린다. 방화벽이 패킷을
#:   조용히 버리는 경우가 그렇다.
#:
#:   실측 (2026-09-07 · 응답 없는 주소로 접속)
#:       connect_timeout=3   3.1초 만에 ConnectionTimeout
#:       없음                60초가 지나도 안 끝남
#:
#:   그리고 프론트에도 타임아웃이 없어(`api.ts` 에 AbortController 0건)
#:   **끊는 쪽이 아무도 없다.** 화면은 오류도 없이 멈춘 채로 남는다.
#:
#:   5초는 매입이 `purchase_agent/db.py` 에 넣은 값과 맞춘 것이다 (#305).
#:   파트마다 다르면 어느 쪽이 먼저 끊겼는지 화면만 보고 알 수 없다.
#:   — 2026-09-28 부터 값의 자리는 `app/core/db.py::CONNECT_TIMEOUT_SECONDS` 하나이고,
#:   이 이름은 그 값을 가리킨다.
CONNECT_TIMEOUT_SECONDS = core_db.CONNECT_TIMEOUT_SECONDS


def get_db_schema() -> str:
    """설정된 PostgreSQL Schema 이름을 반환한다."""
    return settings.get_db_schema()


def source_database_settings() -> settings.DatabaseSettings:
    """원본 창고 접속 정보. 원자료와 학습 테이블이 있는 곳이다.

    ``ML_SOURCE_DB_*`` 가 있으면 그것을, 없으면 기본 ``DB_*`` 를 쓰되
    데이터베이스 이름만 ``ML_SOURCE_DB_NAME`` 으로 바꾼다.
    같은 서버의 다른 데이터베이스이므로 접속 정보를 두 벌 관리할 이유가 없다.
    """
    settings.load_env_file()
    base = settings.database_settings()
    name = os.getenv("ML_SOURCE_DB_NAME", "").strip()
    if not name:
        raise RuntimeError(
            "ML_SOURCE_DB_NAME 이 필요합니다. 원본 데이터가 있는 데이터베이스 이름입니다."
        )
    return replace(
        base,
        host=os.getenv("ML_SOURCE_DB_HOST", base.host),
        port=os.getenv("ML_SOURCE_DB_PORT", base.port),
        name=name,
        user=os.getenv("ML_SOURCE_DB_USER", base.user),
        password=os.getenv("ML_SOURCE_DB_PASSWORD", base.password),
    )


#: 원본 창고 풀. 접속 대상이 서비스 창고와 달라 따로 둔다. 접속 정보는 처음 열 때 읽는다.
SOURCE_POOL = core_db.DatabasePool("ml-source", source_database_settings)


def _read_connection(*, source: bool) -> AbstractContextManager[Connection]:
    """조회 연결 하나. ``source=True`` 면 **원본 창고 풀**에서, 아니면 서비스 창고 풀에서 빌린다."""
    return SOURCE_POOL.read_connection() if source else core_db.read_connection()


def fetch_all(query: Query, params: Params = None, *, source: bool = False) -> list[dict[str, Any]]:
    """다건 조회. ``source=True`` 면 원본 창고에서 읽는다."""
    with _read_connection(source=source) as conn, conn.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()


def fetch_one(
    query: Query, params: Params = None, *, source: bool = False
) -> dict[str, Any] | None:
    """단건 조회. ``source=True`` 면 원본 창고에서 읽는다."""
    with _read_connection(source=source) as conn, conn.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchone()


def execute_many(query: Query, rows: Sequence[Sequence[object]]) -> int:
    """서비스 창고에 여러 행을 쓴다. 적재된 행 수를 돌려준다.

    한 호출 = 한 트랜잭션. commit 을 눈에 보이게 적는다(종전과 같다) — 빌린 연결은 블록이
    끝나면 돌려준다.
    """
    if not rows:
        return 0
    with core_db.connection() as connection, connection.cursor() as cursor:
        cursor.executemany(query, rows)
        connection.commit()
        return len(rows)
