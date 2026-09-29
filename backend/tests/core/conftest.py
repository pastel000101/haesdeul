"""`app/core` 검사 공용 — **실 DB 에 닿지 않는다**, 그리고 풀이 만드는 연결의 가짜.

★ 실 DB 차단은 루트 `tests/conftest.py::실_DB_연결을_막는다` 가 건다 (`psycopg.connect` ·
  `psycopg.Connection.connect` · psycopg 연결로 여는 풀). 이 폴더는 그 위에서
  **psycopg_pool 의 실제 구현에 가짜 연결 종류(`FakePgConnection`)를 끼워** 연결 모듈이 풀을
  어떻게 쓰는지 잰다. PostgreSQL 이 실제로 commit 하는지는 재지 않는다(실 DB 검증은 미실행).

★ `.env` 가 있는 개발 PC 에서도 결과가 같도록 `.env` 적재를 끈다. 적재를 재는 검사는
  자기 기록기로 다시 바꿔 끼운다.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fake_pg_connection import FakePgConnection

from app.core import db as core_db
from app.core import settings

DB_ENV = {
    "DB_HOST": "db.test",
    "DB_PORT": "5432",
    "DB_NAME": "service_db",
    "DB_USER": "tester",
    "DB_PASSWORD": "secret",
    "DB_SCHEMA": "haetdeul_test",
    # 풀 크기 — 검사가 연결 수를 셀 수 있게 미리 열어 두는 연결을 0으로 둔다.
    "DB_POOL_MIN_SIZE": "0",
    "DB_POOL_MAX_SIZE": "2",
    "DB_POOL_TIMEOUT_SECONDS": "1",
}

@pytest.fixture(autouse=True)
def disable_dotenv_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.logistics.db

    monkeypatch.setattr(settings, "load_dotenv", lambda *_a, **_k: False)
    monkeypatch.setattr(app.logistics.db, "load_dotenv", lambda *_a, **_k: False)


@pytest.fixture
def db_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    for key, value in DB_ENV.items():
        monkeypatch.setenv(key, value)
    for key in (
        "ML_SOURCE_DB_HOST",
        "ML_SOURCE_DB_PORT",
        "ML_SOURCE_DB_NAME",
        "ML_SOURCE_DB_USER",
        "ML_SOURCE_DB_PASSWORD",
    ):
        monkeypatch.delenv(key, raising=False)
    return dict(DB_ENV)


@pytest.fixture
def fake_pg() -> Iterator[type[FakePgConnection]]:
    FakePgConnection.reset()
    yield FakePgConnection
    FakePgConnection.reset()


@pytest.fixture
def service_pool(
    monkeypatch: pytest.MonkeyPatch, db_env: dict[str, str], fake_pg: type[FakePgConnection]
) -> Iterator[core_db.DatabasePool]:
    """서비스 풀을 **가짜 연결 종류를 쓰는 진짜 풀**로 바꿔 끼운다. 끝나면 닫는다."""
    pool = core_db.DatabasePool(
        "test-service", settings.database_settings, connection_class=fake_pg
    )
    monkeypatch.setattr(core_db, "SERVICE_POOL", pool)
    yield pool
    pool.close()
