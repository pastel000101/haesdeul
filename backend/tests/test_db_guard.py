"""루트 가드(`tests/conftest.py::실_DB_연결을_막는다`)가 **풀을 통한 연결도** 막는지 잰다.

🔴 풀 전환(2026-09-29) 뒤에는 `psycopg.connect` 를 막는 것만으로 실 DB 가 막히지 않는다 —
   psycopg_pool 은 작업 스레드에서 `Connection.connect` 를 부른다. 막혔다고 믿지 않고
   **막혔는지를 직접 잰다.** 여기가 빨개지면 가드가 뚫린 것이다.

★ 예외 클래스를 `conftest` 에서 이름으로 가져오지 않는다
  (`tests/master/test_conftest_not_imported_by_name.py`). 문장으로 잰다.
"""

from __future__ import annotations

import time

import psycopg
import psycopg_pool
import pytest

from app.core import db as core_db
from app.core import settings

가드_문장 = "db 마크가 없는 검사가 실 DB 연결"


def test_the_old_door_psycopg_connect_is_blocked() -> None:
    with pytest.raises(AssertionError, match=가드_문장):
        psycopg.connect(host="127.0.0.1", port=9, dbname="guard", connect_timeout=1)


def test_the_door_the_pool_uses_is_blocked() -> None:
    """psycopg_pool 이 새 연결을 만들 때 부르는 문."""
    with pytest.raises(AssertionError, match=가드_문장):
        psycopg.Connection.connect("host=127.0.0.1 port=9 dbname=guard connect_timeout=1")


def test_a_pool_of_real_connections_cannot_open() -> None:
    pool: psycopg_pool.ConnectionPool = psycopg_pool.ConnectionPool(
        "host=127.0.0.1 port=9 dbname=guard", open=False
    )
    with pytest.raises(AssertionError, match=가드_문장):
        pool.open()


def test_the_app_pool_stops_at_the_first_borrow_without_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """접속 정보가 있어도 대여가 **바로** 멈춘다 — 풀의 대기 시간까지 매달리지 않는다."""
    for key, value in {
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "9",
        "DB_NAME": "guard",
        "DB_USER": "guard",
        "DB_PASSWORD": "guard",
        "DB_POOL_TIMEOUT_SECONDS": "30",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(settings, "load_dotenv", lambda *_a, **_k: False)
    pool = core_db.DatabasePool("guard", settings.database_settings)

    started = time.monotonic()
    with pytest.raises(AssertionError, match=가드_문장), pool.connection():
        pass

    assert time.monotonic() - started < 5
    assert not pool.is_open
