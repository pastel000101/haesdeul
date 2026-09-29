"""부서 `db.py` 입구 — 풀 전환 뒤에도 **부서마다 다른 동작**이 그대로인지 (실 DB 연결 없음).

```text
재무·영업·물류  조회는 서비스 풀의 조회 연결 · RETURNING 쓰기는 한 호출 = 한 트랜잭션
ML             서비스 풀 + **원본 창고 풀**(따로) · execute_many 는 명시 commit
매입           조회만 · 쓰기·스키마 헬퍼 없음 (test_auction_quotes.py 가 이름을 잠근다)
물류           스키마 이름을 읽을 때 .env 를 프로세스에서 한 번만 적재
```

★ 연결은 `service_pool` fixture 가 바꿔 끼운 **진짜 psycopg_pool 풀 + 가짜 연결 종류**다
  (`tests/core/conftest.py`). 종전 «부서 읽기 범위» 검사는 없앴다 — 그 범위가 하던 연결
  재사용을 이제 풀이 하고, 그것은 `test_core_db.py` 가 잰다.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row
from 풀_가짜연결 import FakePgConnection

import app.finance.db as finance_db
import app.logistics.db as logistics_db
import app.ml.db as ml_db
import app.purchase_agent.db as purchase_db
import app.sales.db as sales_db
from app.core import db as core_db
from app.core import settings

READ_MODULES = [finance_db, sales_db, logistics_db, purchase_db, ml_db]
WRITE_MODULES = [finance_db, sales_db, logistics_db]


@pytest.mark.parametrize("module", READ_MODULES, ids=lambda m: m.__name__)
def test_reads_borrow_a_read_connection_from_the_shared_pool_and_return_it(
    module, service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    fake_pg.one = {"a": 1}
    fake_pg.rows = [{"a": 1}]

    assert module.fetch_all("SELECT", (1,)) == [{"a": 1}]
    assert module.fetch_one("SELECT", (2,)) == {"a": 1}

    (conn,) = fake_pg.made  # 두 조회가 한 연결을 다시 썼다
    assert conn.executed == [("SELECT", (1,)), ("SELECT", (2,))]
    assert "commit" not in conn.events  # 조회는 트랜잭션을 열지 않는다
    assert conn.autocommit is False  # 돌려받은 연결은 쓰기용으로 되돌려져 있다


@pytest.mark.parametrize("module", READ_MODULES, ids=lambda m: m.__name__)
def test_a_failed_read_returns_the_connection_and_keeps_the_error(
    module, service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    with core_db.connection() as warm:
        warm.fail_on_execute = psycopg.errors.UndefinedTable("no table")

    with pytest.raises(psycopg.errors.UndefinedTable):
        module.fetch_all("SELECT")

    (conn,) = fake_pg.made
    conn.fail_on_execute = None
    assert module.fetch_all("SELECT") == []  # 다음 조회가 같은 연결로 멀쩡히 돈다


@pytest.mark.parametrize("module", WRITE_MODULES, ids=lambda m: m.__name__)
def test_returning_write_is_one_transaction_on_its_own_connection(
    module, service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    """쓰기는 남이 쥔 연결에 얹히지 않는다 — 얹히면 커밋 시점이 바뀐다."""
    fake_pg.one = {"id": "X"}

    with core_db.connection() as held:
        _ = held.cursor().execute("SELECT held")
        assert module.execute_returning_one("INSERT", ("v",)) == {"id": "X"}

    write = next(c for c in fake_pg.made if c is not held)
    assert write.executed == [("INSERT", ("v",))]
    assert write.events[-1] == "commit"
    assert "commit" not in held.events


@pytest.mark.parametrize("module", WRITE_MODULES, ids=lambda m: m.__name__)
def test_returning_write_without_a_row_rolls_back(
    module, service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    """RETURNING 행이 없으면 종전 문구로 멈추고, 그 예외로 쓰기가 rollback 된다."""
    with pytest.raises(RuntimeError) as raised:
        module.execute_returning_one("INSERT")

    assert str(raised.value) == "Database write did not return a row"
    (conn,) = fake_pg.made
    assert conn.events[-1] == "rollback"
    assert "commit" not in conn.events


@pytest.mark.parametrize("module", WRITE_MODULES, ids=lambda m: m.__name__)
def test_returning_write_failure_rolls_back_and_keeps_the_error(
    module, service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    with core_db.connection() as warm:
        warm.fail_on_execute = psycopg.errors.UniqueViolation("dup")

    with pytest.raises(psycopg.errors.UniqueViolation):
        module.execute_returning_one("INSERT")

    (conn,) = fake_pg.made
    assert conn.events[-1] == "rollback"
    assert "commit" not in conn.events


def test_every_department_shares_the_one_service_pool(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    """같은 접속 대상 · 같은 계정이라 부서별 풀을 따로 만들지 않는다."""
    for module in READ_MODULES:
        module.fetch_all("Q")

    assert len(fake_pg.connects) == 1


@pytest.mark.parametrize("module", READ_MODULES, ids=lambda m: m.__name__)
def test_missing_env_message_is_unchanged(
    module, monkeypatch: pytest.MonkeyPatch, fake_pg: type[FakePgConnection]
) -> None:
    """기준선 실패 21건의 원인 문구가 이것이다 — 바뀌면 원인 대조가 깨진다."""
    for key in settings.DB_CONNECTION_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    pool = core_db.DatabasePool("t-missing", settings.database_settings, connection_class=fake_pg)
    monkeypatch.setattr(core_db, "SERVICE_POOL", pool)

    with pytest.raises(RuntimeError) as raised:
        module.fetch_all("Q")

    assert str(raised.value) == (
        "Missing required database environment variables: "
        "DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD"
    )


@pytest.mark.parametrize(
    "module", [finance_db, sales_db, ml_db, logistics_db], ids=lambda m: m.__name__
)
def test_schema_comes_from_db_schema(module, db_env: dict[str, str]) -> None:
    assert module.get_db_schema() == "haetdeul_test"


def test_logistics_still_loads_env_once_and_not_through_the_shared_loader(
    monkeypatch: pytest.MonkeyPatch, db_env: dict[str, str]
) -> None:
    shared: list[object] = []
    own: list[object] = []
    monkeypatch.setattr(settings, "load_dotenv", lambda path: shared.append(path))
    monkeypatch.setattr(logistics_db, "load_dotenv", lambda path: own.append(path))
    monkeypatch.setattr(logistics_db, "_env_file_loaded", False)

    for _ in range(3):
        logistics_db.get_db_schema()

    assert own == [settings.ENV_FILE]
    assert shared == []


def test_the_pool_reads_connection_settings_once_not_per_borrow(
    monkeypatch: pytest.MonkeyPatch,
    service_pool: core_db.DatabasePool,
) -> None:
    """종전에는 연결마다 `.env` 를 읽었다(물류는 대시보드 한 번에 550회). 이제 풀을 열 때 한 번."""
    loads: list[object] = []
    monkeypatch.setattr(settings, "load_dotenv", lambda path: loads.append(path))

    for _ in range(3):
        finance_db.fetch_all("Q")
        logistics_db.fetch_all("Q")

    # 접속 정보 1 + 풀 크기 1 — 대여마다 늘지 않는다
    assert loads == [settings.ENV_FILE, settings.ENV_FILE]


# ── ML 전용 ─────────────────────────────────────────────────────────────────


@pytest.fixture
def source_pool(
    monkeypatch: pytest.MonkeyPatch, fake_pg: type[FakePgConnection]
) -> Any:
    pool = core_db.DatabasePool(
        "test-ml-source", ml_db.source_database_settings, connection_class=fake_pg
    )
    monkeypatch.setattr(ml_db, "SOURCE_POOL", pool)
    yield pool
    pool.close()


def test_ml_source_settings_inherit_db_env_and_swap_the_database(
    monkeypatch: pytest.MonkeyPatch, db_env: dict[str, str]
) -> None:
    monkeypatch.setenv("ML_SOURCE_DB_NAME", " raw_db ")

    assert ml_db.source_database_settings() == settings.DatabaseSettings(
        host="db.test", port="5432", name="raw_db", user="tester", password="secret"
    )


def test_ml_source_settings_prefer_ml_source_env(
    monkeypatch: pytest.MonkeyPatch, db_env: dict[str, str]
) -> None:
    for key, value in {
        "ML_SOURCE_DB_NAME": "raw_db",
        "ML_SOURCE_DB_HOST": "raw.host",
        "ML_SOURCE_DB_PORT": "6543",
        "ML_SOURCE_DB_USER": "raw_user",
        "ML_SOURCE_DB_PASSWORD": "raw_pw",
    }.items():
        monkeypatch.setenv(key, value)

    assert ml_db.source_database_settings() == settings.DatabaseSettings(
        host="raw.host", port="6543", name="raw_db", user="raw_user", password="raw_pw"
    )


def test_ml_source_requires_a_database_name(
    db_env: dict[str, str], source_pool: core_db.DatabasePool
) -> None:
    with pytest.raises(RuntimeError, match="ML_SOURCE_DB_NAME 이 필요합니다"):
        ml_db.fetch_all("S", source=True)
    assert not source_pool.is_open


def test_ml_keeps_service_and_source_warehouses_in_separate_pools(
    monkeypatch: pytest.MonkeyPatch,
    service_pool: core_db.DatabasePool,
    source_pool: core_db.DatabasePool,
    fake_pg: type[FakePgConnection],
) -> None:
    """🔴 섞으면 서비스 질의가 원본 창고로 가서 «표가 없다» 가 된다."""
    monkeypatch.setenv("ML_SOURCE_DB_NAME", "raw_db")

    ml_db.fetch_all("S1", source=True)
    ml_db.fetch_all("V1")
    ml_db.fetch_one("S2", source=True)
    ml_db.fetch_one("V2")

    by_db = {c.kwargs["dbname"]: c for c in fake_pg.made}
    assert set(by_db) == {"service_db", "raw_db"}
    assert by_db["service_db"].executed == [("V1", None), ("V2", None)]
    assert by_db["raw_db"].executed == [("S1", None), ("S2", None)]
    assert by_db["raw_db"].kwargs["row_factory"] is dict_row


def test_ml_execute_many_commits_explicitly_and_counts_rows(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    assert ml_db.execute_many("UPSERT", [(1,), (2,)]) == 2
    assert ml_db.execute_many("UPSERT", []) == 0

    (conn,) = fake_pg.made  # 빈 행은 연결을 빌리지 않는다
    # 명시 commit 한 번 — 반환은 commit 을 더하지 않는다
    assert conn.events == ["check", "executemany", "commit"]
    assert conn.executed == [("UPSERT", [(1,), (2,)])]


# ── 매입 전용 ────────────────────────────────────────────────────────────────


def test_purchase_module_does_not_carry_write_helpers_from_core() -> None:
    """매입은 read-only(규칙 2). core 에서 조회 대여만 골라 와, 쓰기 대여·경계가 이름공간에 없다."""
    carried = {getattr(value, "__name__", "") for value in vars(purchase_db).values()}
    assert "transaction" not in carried
    assert "connection" not in carried
    assert not hasattr(purchase_db, "core_db")
    assert not hasattr(purchase_db, "get_db_schema")
