"""`app/core/db.py` — 풀 준비 · 연결 대여 · 반환 · 종료 (실 DB 연결 없음).

★ 풀은 **psycopg_pool 3.3.3 의 실제 `ConnectionPool`** 이고, 연결만 `tests/core/conftest.py` 의
  `FakePgConnection` 이다. 그래서 이 검사가 재는 것은 **연결 모듈이 풀을 언제 · 어떻게 쓰는가**
  (몇 번 여는가 · 돌려주는가 · 언제 commit/rollback 하는가)이고, PostgreSQL 이 실제로
  commit 하는지 · 속도가 나아졌는지는 재지 않는다(실 DB 검증은 미실행).

★ SQL 을 실행하는 조회·쓰기 함수는 부서 `db.py` 에 있다 — 그 검사는
  `test_department_db_entrypoints.py` 에 있다.
"""

from __future__ import annotations

import ast
import threading
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fake_pg_connection import INTRANS, UNKNOWN, FakePgConnection
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import PoolTimeout

import app
from app.core import db as core_db
from app.core import settings

APP = Path(app.__file__).resolve().parent


def _select(conn: Any, query: str = "Q") -> None:
    with conn.cursor() as cursor:
        cursor.execute(query)
        cursor.fetchall()


# ── 준비 ─────────────────────────────────────────────────────────────────────


def test_first_borrow_opens_the_pool_with_the_department_connection_arguments(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    assert not service_pool.is_open

    with core_db.connection():
        pass

    assert service_pool.is_open
    assert fake_pg.connects == [
        {
            "host": "db.test",
            "port": "5432",
            "dbname": "service_db",
            "user": "tester",
            "password": "secret",
            "row_factory": dict_row,
            "connect_timeout": 5,
        }
    ]


def test_missing_connection_env_stops_before_opening_the_pool(
    monkeypatch: pytest.MonkeyPatch, fake_pg: type[FakePgConnection]
) -> None:
    for key in settings.DB_CONNECTION_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    pool = core_db.DatabasePool("t-missing", settings.database_settings, connection_class=fake_pg)

    with pytest.raises(RuntimeError) as raised, pool.connection():
        pass

    assert isinstance(raised.value, settings.MissingDatabaseEnvironment)
    assert str(raised.value) == (
        "Missing required database environment variables: "
        "DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD"
    )
    assert not pool.is_open
    assert fake_pg.connects == []


def test_pool_settings_are_read_when_the_pool_opens(
    monkeypatch: pytest.MonkeyPatch, db_env: dict[str, str], fake_pg: type[FakePgConnection]
) -> None:
    """풀 크기는 설정이다 — 상한 1이면 두 번째 동시 대여는 기다리다 시간 초과가 난다."""
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "1")
    monkeypatch.setenv("DB_POOL_TIMEOUT_SECONDS", "0.2")
    pool = core_db.DatabasePool("t-size", settings.database_settings, connection_class=fake_pg)
    try:
        with pool.connection(), pytest.raises(PoolTimeout), pool.connection():
            pass
    finally:
        pool.close()

    assert issubclass(PoolTimeout, psycopg.OperationalError)  # 화면의 «DB 못 닿음» 분류 그대로


# ── 빌려 준다 · 돌려받는다 ───────────────────────────────────────────────────


def test_repeated_borrows_reuse_one_connection(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    """반복 작업이 연결을 다시 쓴다 — 종전에는 대여마다 `psycopg.connect` 였다."""
    seen = []
    for _ in range(3):
        with core_db.connection() as conn:
            seen.append(conn)
        with core_db.read_connection() as conn:
            seen.append(conn)

    assert len(fake_pg.connects) == 1
    assert all(conn is seen[0] for conn in seen)
    assert "close" not in seen[0].events


def test_return_does_not_commit_and_ends_a_leftover_transaction(
    service_pool: core_db.DatabasePool,
) -> None:
    """🔴 반환은 commit 이 아니다. 끝나지 않은 트랜잭션은 되돌린 뒤 돌려준다."""
    with core_db.connection() as conn:
        _select(conn)
        assert conn.pgconn.transaction_status == INTRANS

    assert conn.events[-1] == "rollback"
    assert "commit" not in conn.events
    with core_db.connection() as again:
        assert again is conn  # 버리지 않고 돌려받았다


def test_a_block_that_raises_still_returns_the_connection(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    with pytest.raises(ValueError, match="boom"), core_db.connection() as conn:
        _select(conn)
        raise ValueError("boom")

    assert conn.events[-1] == "rollback"
    with core_db.connection() as again:
        assert again is conn
    assert len(fake_pg.connects) == 1


def test_explicit_commit_is_the_callers_and_return_adds_nothing(
    service_pool: core_db.DatabasePool,
) -> None:
    """마스터 경계 함수 모양 — commit 을 눈에 보이게 하고, 반환은 아무것도 더하지 않는다."""
    with core_db.connection() as conn:
        _select(conn)
        conn.commit()

    assert conn.events == ["check", "execute", "commit"]


def test_a_connection_broken_inside_the_block_is_discarded_and_replaced(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    with core_db.connection() as conn:
        conn.break_()

    # ★ 새 연결을 몇 개 만드는지는 풀의 작업 스레드 사정이라 세지 않는다 — 버려진 연결을
    #   다시 빌려 주지 않는다는 것만 잰다.
    for _ in range(3):
        with core_db.connection() as fresh:
            assert fresh is not conn
            assert not fresh.closed
    assert len(fake_pg.connects) >= 2


def test_a_dead_idle_connection_is_checked_and_replaced_before_it_is_lent(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    """쉬는 동안 끊긴 연결을 빌려 주지 않는다 — 종전 «매번 새 연결» 과 같은 결과."""
    with core_db.connection() as conn:
        pass
    conn.break_()  # 풀 안에서 쉬다가 끊겼다

    with core_db.connection() as fresh:
        assert fresh is not conn
        assert not fresh.closed


def test_concurrent_borrows_get_different_connections(
    service_pool: core_db.DatabasePool,
) -> None:
    """쥐고 있는 연결을 다른 대여에 빌려 주지 않는다 — 한 연결을 두 흐름이 나눠 쓰지 않는다."""
    with core_db.connection() as held, core_db.read_connection() as other:
        assert other is not held


def test_threads_borrow_their_own_connections(service_pool: core_db.DatabasePool) -> None:
    got: list[Any] = []
    release = threading.Event()

    def worker() -> None:
        with core_db.connection() as conn:
            got.append(conn)
            release.wait(2)

    with core_db.connection() as mine:
        thread = threading.Thread(target=worker)
        thread.start()
        while not got and thread.is_alive():
            thread.join(0.01)
        release.set()
        thread.join()

    assert got and got[0] is not mine


# ── 트랜잭션 경계 ────────────────────────────────────────────────────────────


def test_transaction_commits_on_success_without_returning_the_connection(
    service_pool: core_db.DatabasePool,
) -> None:
    with core_db.connection() as conn:
        with core_db.transaction(conn):
            _select(conn)
        assert conn.events[-1] == "commit"
        assert not conn.closed


def test_transaction_rolls_back_and_reraises(service_pool: core_db.DatabasePool) -> None:
    with core_db.connection() as conn:
        with pytest.raises(ValueError, match="boom"), core_db.transaction(conn):
            _select(conn)
            raise ValueError("boom")
        assert conn.events[-1] == "rollback"
        assert "commit" not in conn.events


def test_a_rollback_error_does_not_hide_the_real_error(service_pool: core_db.DatabasePool) -> None:
    with core_db.connection() as conn:

        def broken_rollback() -> None:
            raise psycopg.OperationalError("already gone")

        conn.rollback = broken_rollback  # type: ignore[method-assign]
        with pytest.raises(ValueError, match="real"), core_db.transaction(conn):
            raise ValueError("real")


def test_transaction_commit_failure_is_raised(service_pool: core_db.DatabasePool) -> None:
    """commit 이 실패하면 올린다 — 조용히 성공으로 넘기지 않는다."""
    with core_db.connection() as conn:

        def failing_commit() -> None:
            raise psycopg.errors.SerializationFailure("conflict")

        conn.commit = failing_commit  # type: ignore[method-assign]
        with pytest.raises(psycopg.errors.SerializationFailure), core_db.transaction(conn):
            _select(conn)


# ── 조회 전용 대여 ───────────────────────────────────────────────────────────


def test_read_connection_runs_statements_without_a_transaction_and_restores(
    service_pool: core_db.DatabasePool,
) -> None:
    with core_db.read_connection() as conn:
        assert conn.autocommit is True
        _select(conn)
        assert conn.pgconn.transaction_status != INTRANS

    assert conn.autocommit is False  # 돌려받은 연결은 다음 쓰기가 트랜잭션을 쓰게 되돌린다
    assert "commit" not in conn.events
    with core_db.connection() as again:
        assert again is conn
        assert again.autocommit is False


def test_a_failed_read_does_not_poison_the_next_borrow(
    service_pool: core_db.DatabasePool,
) -> None:
    with core_db.read_connection() as conn:
        conn.fail_on_execute = psycopg.errors.UndefinedTable("no table")
        with pytest.raises(psycopg.errors.UndefinedTable):
            _select(conn)
        conn.fail_on_execute = None

    with core_db.connection() as again:
        _select(again)  # 실패한 트랜잭션에 얹히지 않는다
        assert again.pgconn.transaction_status == INTRANS


# ── 다른 접속 대상 ───────────────────────────────────────────────────────────


def test_pools_for_different_targets_never_share_connections(
    service_pool: core_db.DatabasePool,
    fake_pg: type[FakePgConnection],
    db_env: dict[str, str],
) -> None:
    other = core_db.DatabasePool(
        "t-other",
        lambda: settings.DatabaseSettings("raw.host", "6543", "raw_db", "raw_user", "raw_pw"),
        connection_class=fake_pg,
    )
    try:
        for _ in range(2):
            with core_db.connection() as service, other.connection() as raw:
                assert service is not raw
                assert (service.kwargs["dbname"], raw.kwargs["dbname"]) == ("service_db", "raw_db")
    finally:
        other.close()

    assert sorted(c["dbname"] for c in fake_pg.connects) == ["raw_db", "service_db"]


# ── 정리 ─────────────────────────────────────────────────────────────────────


def test_close_pools_closes_idle_connections_and_the_next_borrow_reopens(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    with core_db.connection() as first:
        pass

    core_db.close_pools()

    assert not service_pool.is_open
    assert first.closed
    with core_db.connection() as second:
        assert second is not first
    assert len(fake_pg.connects) == 2


def test_a_connection_lent_when_the_pool_closes_is_closed_on_return(
    service_pool: core_db.DatabasePool,
) -> None:
    with core_db.connection() as conn:
        core_db.close_pools()
        assert not conn.closed  # 쓰고 있는 동안에는 닫지 않는다

    assert conn.closed


def test_pool_lifespan_opens_the_service_pool_and_closes_every_pool_even_on_error(
    service_pool: core_db.DatabasePool,
) -> None:
    with pytest.raises(RuntimeError, match="boom"), core_db.pool_lifespan():
        assert service_pool.is_open
        with core_db.connection() as conn:
            pass
        raise RuntimeError("boom")

    assert not service_pool.is_open
    assert conn.closed


def test_pool_lifespan_without_db_settings_starts_and_the_first_borrow_fails_as_before(
    monkeypatch: pytest.MonkeyPatch, fake_pg: type[FakePgConnection]
) -> None:
    """DB 설정이 없는 자리(검사 · DB 없는 개발 PC)에서도 앱 · CLI 는 뜬다."""
    for key in settings.DB_CONNECTION_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    pool = core_db.DatabasePool("t-lifespan", settings.database_settings, connection_class=fake_pg)
    monkeypatch.setattr(core_db, "SERVICE_POOL", pool)

    with core_db.pool_lifespan():
        assert not pool.is_open
        with pytest.raises(RuntimeError, match="Missing required database"), core_db.connection():
            pass


def test_pool_lifespan_does_not_swallow_a_bad_pool_setting(
    monkeypatch: pytest.MonkeyPatch, service_pool: core_db.DatabasePool
) -> None:
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "0")

    with pytest.raises(ValueError, match="DB_POOL_"), core_db.pool_lifespan():
        pass


def test_the_app_lifespan_opens_and_closes_the_pool(service_pool: core_db.DatabasePool) -> None:
    from app.main import app as fastapi_app

    with TestClient(fastapi_app) as client:
        assert client.get("/health").status_code == 200
        assert service_pool.is_open

    assert not service_pool.is_open


# ── HTTP 입구 — Depends ──────────────────────────────────────────────────────


def _http_app() -> FastAPI:
    api = FastAPI()
    DbConnection = Depends(core_db.db_connection, scope="function")

    @api.post("/ok")
    def ok(conn: Any = DbConnection) -> dict[str, str]:
        with core_db.transaction(conn):
            _select(conn, "INSERT")
        return {"status": "ok"}

    @api.post("/conflict")
    def conflict(conn: Any = DbConnection) -> dict[str, str]:
        with core_db.transaction(conn):
            _select(conn, "INSERT")
            raise HTTPException(status_code=409, detail="conflict")

    @api.get("/read")
    def read(conn: Any = DbConnection) -> dict[str, str]:
        _select(conn, "SELECT")
        return {"status": "read"}

    return api


def test_http_dependency_lends_one_connection_and_commits_only_where_the_handler_says(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    client = TestClient(_http_app())

    assert client.post("/ok").status_code == 200
    assert client.post("/conflict").status_code == 409
    assert client.get("/read").status_code == 200

    (conn,) = fake_pg.made  # 세 요청이 한 연결을 차례로 다시 썼다
    assert conn.events == [
        "check", "execute", "commit",       # /ok       — 핸들러의 트랜잭션이 commit
        "check", "execute", "rollback",     # /conflict — 예외로 rollback
        "check", "execute", "rollback",     # /read     — 반환은 commit 이 아니다
    ]  # fmt: skip


# ── 구조 ─────────────────────────────────────────────────────────────────────


def _app_sources() -> list[tuple[str, ast.Module]]:
    out = []
    for path in sorted(APP.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        out.append((path.relative_to(APP).as_posix(), ast.parse(path.read_text(encoding="utf-8"))))
    return out


def test_nothing_in_the_app_opens_a_connection_outside_the_pool() -> None:
    """앱 안에 `psycopg.connect` 부름이 없다 — 연결은 `core/db.py` 의 풀만 만든다."""
    sites = []
    for name, tree in _app_sources():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "connect"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"psycopg", "Connection"}
            ):
                sites.append(name)
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "psycopg"
                and any(alias.name == "connect" for alias in node.names)
            ):
                sites.append(f"{name} (from psycopg import connect)")
    assert sites == []


def test_connection_pools_are_created_only_in_core_db() -> None:
    sites = sorted(
        name
        for name, tree in _app_sources()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ConnectionPool"
    )
    assert sites == ["core/db.py"]


def test_borrowed_connections_are_not_closed_or_used_as_commit_blocks() -> None:
    """🔴 빌린 연결을 `close()` 하거나 `with conn:` 으로 쓰지 않는다.

    풀은 `close()` 를 반환으로 바꾸지 않고(진짜로 닫혀 버려진다), psycopg 의 `with conn:` 은
    풀 연결이면 commit 만 하고 돌려주지 않는다 (psycopg 3.3.4 · psycopg_pool 3.3.3 소스).
    """
    names = {"conn", "connection", "own", "owned_connection", "other"}
    found = []
    for name, tree in _app_sources():
        if name == "core/db.py":
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "close"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in names
            ):
                found.append(f"{name}: {node.func.value.id}.close()")
            if isinstance(node, ast.With):
                for item in node.items:
                    expr = item.context_expr
                    if isinstance(expr, ast.Name) and expr.id in names:
                        found.append(f"{name}: with {expr.id}:")
    assert found == []


def test_the_connection_module_does_not_run_sql() -> None:
    """연결 모듈은 풀 준비 · 대여 · 반환 · 정리만 한다. SQL 실행은 연결을 받은 쪽의 일이다.

    조회·쓰기 헬퍼가 여기로 다시 모이면 "연결을 어디서 얻나" 와 "SQL 이 어디서 도나" 가
    한 파일에 섞여, 조회 한 건을 따라가려고 파일을 여러 번 오가게 된다 (2026-09-28 방향 수정).
    """
    tree = ast.parse((APP / "core" / "db.py").read_text(encoding="utf-8"))
    sql_calls = {"execute", "executemany", "fetchone", "fetchall", "fetchmany", "cursor"}
    found = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in sql_calls
    ]
    assert found == []


def test_core_does_not_import_department_packages_or_fastapi() -> None:
    """core 는 부서를 모른다 — 의존은 부서 → core 한 방향이다. HTTP(`Depends`)는 입구의 일이다."""
    for path in sorted((APP / "core").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            for name in names:
                if name.startswith("app."):
                    assert name.startswith("app.core"), f"{path.name} imports {name}"
                assert not name.startswith("fastapi"), f"{path.name} imports {name}"


def test_connection_status_constants_match_psycopg() -> None:
    """가짜 연결이 쓰는 상태 값이 psycopg 의 것과 같다 — 가짜가 따로 놀지 않게."""
    assert UNKNOWN == psycopg.pq.TransactionStatus.UNKNOWN


@pytest.mark.parametrize(
    ("error", "unavailable"),
    [
        (psycopg.OperationalError("connection refused"), True),
        (PoolTimeout("couldn't get a connection after 5.00 sec"), True),
        (psycopg.ProgrammingError("syntax error"), False),
        (psycopg.IntegrityError("duplicate key"), False),
        (ValueError("lineage broken"), False),
        (RuntimeError("Database write did not return a row"), False),
    ],
    ids=["operational", "pool-timeout", "programming", "integrity", "value", "runtime"],
)
def test_is_unavailable_is_true_only_for_failures_to_reach_the_db(
    error: BaseException, unavailable: bool
) -> None:
    """DB 에 **닿지 못한** 실패만 참이다 — 화면이 503(다시 오면 될 수 있다)과 500 을 가른다.

    ★ 2026-09-30 재구성 BL-019: 화면 물류 탭이 들고 있던 `psycopg.OperationalError` 분류를
      연결 모듈로 옮겼다. SQL 이 틀렸거나 무결성이 깨진 것은 우리 코드가 깨진 것이라 참이 아니다.
    """
    assert core_db.is_unavailable(error) is unavailable
