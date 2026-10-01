"""`app/core/settings.py` — `.env` 위치·적재 시점·필수 환경변수 읽기 (실 DB 연결 없음).

부서 `db.py` 다섯 벌에 있던 `_required_environment` 의 의미가 그대로인지 잰다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import app
from app.core import settings


def test_env_file_is_backend_dot_env() -> None:
    """부서 `db.py` 들이 가리키던 `backend/.env` 와 같은 파일이다."""
    backend = Path(app.__file__).resolve().parent.parent
    assert settings.ENV_FILE == backend / ".env"


def test_default_load_reads_the_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(settings, "load_dotenv", lambda path: calls.append(path))

    settings.load_env_file()

    assert calls == [settings.ENV_FILE]


def test_loads_on_every_call_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """재무·영업·매입·ML 의 종전 동작 — 부를 때마다 `.env` 를 적재한다."""
    calls: list[object] = []
    monkeypatch.setattr(settings, "load_dotenv", lambda path: calls.append(path))
    monkeypatch.setenv("DB_SCHEMA", "s1")

    for _ in range(3):
        settings.required_database_environment(("DB_SCHEMA",))

    assert calls == [settings.ENV_FILE] * 3


def test_values_follow_the_environment_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_SCHEMA", "first")
    assert settings.required_database_environment(("DB_SCHEMA",)) == {"DB_SCHEMA": "first"}

    monkeypatch.setenv("DB_SCHEMA", "second")
    assert settings.required_database_environment(("DB_SCHEMA",)) == {"DB_SCHEMA": "second"}


def test_caller_chooses_the_load_function(monkeypatch: pytest.MonkeyPatch) -> None:
    """물류처럼 한 번만 적재하는 부서가 자기 적재 함수를 넘길 수 있다."""
    loaded: list[str] = []
    monkeypatch.setenv("DB_SCHEMA", "x")

    settings.required_database_environment(("DB_SCHEMA",), load=lambda: loaded.append("once"))

    assert loaded == ["once"]


def test_missing_and_empty_values_stop_with_the_same_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """오류 문구가 종전과 한 글자도 같아야 기준선의 실패 원인 대조가 가능하다."""
    monkeypatch.delenv("DB_HOST", raising=False)
    monkeypatch.setenv("DB_PORT", "")
    monkeypatch.setenv("DB_NAME", "n")

    with pytest.raises(RuntimeError) as raised:
        settings.required_database_environment(("DB_HOST", "DB_PORT", "DB_NAME"))

    assert str(raised.value) == "Missing required database environment variables: DB_HOST, DB_PORT"


def test_database_settings_reads_db_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {
        "DB_HOST": "h",
        "DB_PORT": "1",
        "DB_NAME": "n",
        "DB_USER": "u",
        "DB_PASSWORD": "p",
    }.items():
        monkeypatch.setenv(key, value)

    assert settings.database_settings() == settings.DatabaseSettings(
        host="h", port="1", name="n", user="u", password="p"
    )


def test_database_settings_and_schema_use_the_given_load(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded: list[str] = []
    for key in (*settings.DB_CONNECTION_ENV_KEYS, "DB_SCHEMA"):
        monkeypatch.setenv(key, "x")

    settings.database_settings(load=lambda: loaded.append("settings"))
    assert settings.get_db_schema(load=lambda: loaded.append("schema")) == "x"

    assert loaded == ["settings", "schema"]


def test_schema_missing_message_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DB_SCHEMA", raising=False)

    with pytest.raises(RuntimeError) as raised:
        settings.get_db_schema()

    assert str(raised.value) == "Missing required database environment variables: DB_SCHEMA"


# ── 풀 크기 · 대기 시간 (2026-09-29) ──────────────────────────────────────


def test_pool_settings_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in settings.DB_POOL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    assert settings.pool_settings() == settings.DEFAULT_POOL_SETTINGS
    assert settings.DEFAULT_POOL_SETTINGS == settings.PoolSettings(
        min_size=1, max_size=10, timeout_seconds=5.0
    )


def test_pool_settings_follow_the_environment_and_treat_blank_as_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DB_POOL_MIN_SIZE", "2")
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "4")
    monkeypatch.setenv("DB_POOL_TIMEOUT_SECONDS", " ")

    assert settings.pool_settings() == settings.PoolSettings(
        min_size=2, max_size=4, timeout_seconds=5.0
    )


def test_pool_wait_is_shorter_than_the_screen_read_timeout() -> None:
    """백엔드가 프론트(읽기 20초)보다 먼저 포기해야 사유가 화면에 실린다.

    연결 타임아웃(`CONNECT_TIMEOUT_SECONDS`)을 `0 < 값 < 15` 로 잠그는 것과 같은 이유다.
    """
    assert 0 < settings.DEFAULT_POOL_SETTINGS.timeout_seconds < 15


@pytest.mark.parametrize(
    ("minimum", "maximum", "timeout"),
    [("x", "10", "5"), ("3", "2", "5"), ("0", "0", "5"), ("-1", "2", "5"), ("1", "2", "0")],
)
def test_bad_pool_settings_stop_before_the_pool_opens(
    monkeypatch: pytest.MonkeyPatch, minimum: str, maximum: str, timeout: str
) -> None:
    monkeypatch.setenv("DB_POOL_MIN_SIZE", minimum)
    monkeypatch.setenv("DB_POOL_MAX_SIZE", maximum)
    monkeypatch.setenv("DB_POOL_TIMEOUT_SECONDS", timeout)

    with pytest.raises(ValueError, match="DB_POOL_"):
        settings.pool_settings()


# ── 재접속 · 연결 확인 시간과 TCP 상태 확인 (2026-10-01 BL-010 사용자 결정) ─────────────


def test_connection_health_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in settings.DB_CONNECTION_HEALTH_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    assert settings.connection_health_settings() == settings.ConnectionHealthSettings(
        reconnect_timeout_seconds=15.0,
        check_timeout_seconds=5.0,
        keepalives=True,
        keepalives_idle_seconds=30,
        keepalives_interval_seconds=10,
        keepalives_count=3,
        tcp_user_timeout_ms=30_000,
    )


def test_connection_health_follows_the_environment_and_treats_blank_as_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DB_POOL_RECONNECT_TIMEOUT_SECONDS", "20")
    monkeypatch.setenv("DB_POOL_CHECK_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("DB_TCP_KEEPALIVES", "0")
    monkeypatch.setenv("DB_TCP_KEEPALIVES_IDLE_SECONDS", " ")
    monkeypatch.setenv("DB_TCP_KEEPALIVES_INTERVAL_SECONDS", "5")
    monkeypatch.setenv("DB_TCP_KEEPALIVES_COUNT", "4")
    monkeypatch.setenv("DB_TCP_USER_TIMEOUT_MS", "0")

    assert settings.connection_health_settings() == settings.ConnectionHealthSettings(
        reconnect_timeout_seconds=20.0,
        check_timeout_seconds=2.5,
        keepalives=False,
        keepalives_idle_seconds=30,
        keepalives_interval_seconds=5,
        keepalives_count=4,
        tcp_user_timeout_ms=0,
    )


def test_the_check_limit_is_shorter_than_the_screen_read_timeout() -> None:
    """빌려 주기 전 확인도 프론트 읽기(20초)보다 먼저 끝나야 백엔드 사유가 화면에 실린다."""
    assert 0 < settings.DEFAULT_CONNECTION_HEALTH_SETTINGS.check_timeout_seconds < 15


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("DB_POOL_RECONNECT_TIMEOUT_SECONDS", "0"),
        ("DB_POOL_CHECK_TIMEOUT_SECONDS", "-1"),
        ("DB_POOL_CHECK_TIMEOUT_SECONDS", "x"),
        ("DB_TCP_KEEPALIVES", "yes"),
        ("DB_TCP_KEEPALIVES_IDLE_SECONDS", "0"),
        ("DB_TCP_KEEPALIVES_COUNT", "1.5"),
        ("DB_TCP_USER_TIMEOUT_MS", "-5"),
    ],
)
def test_bad_connection_health_settings_stop_before_the_pool_opens(
    monkeypatch: pytest.MonkeyPatch, key: str, value: str
) -> None:
    for name in settings.DB_CONNECTION_HEALTH_ENV_KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(key, value)

    with pytest.raises(ValueError, match="DB 연결 상태 확인"):
        settings.connection_health_settings()


def test_missing_database_environment_is_still_a_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """종류를 따로 두었지만 `RuntimeError` 로 잡던 자리는 그대로 잡는다."""
    monkeypatch.delenv("DB_SCHEMA", raising=False)

    with pytest.raises(settings.MissingDatabaseEnvironment):
        settings.get_db_schema()
    assert issubclass(settings.MissingDatabaseEnvironment, RuntimeError)
