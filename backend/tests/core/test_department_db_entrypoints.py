"""부서 `db.py` 입구 — 풀 전환 뒤에도 **부서마다 다른 동작**이 그대로인지 (실 DB 연결 없음).

```text
마스터         입구 `master/db.py` 가 없다 (2026-09-30 재구성 BL-018) — 조회는 readmodel 이
               조회 연결을, 실행 이력 쓰기는 service 가 연결 하나 · 트랜잭션 하나를 빌려
               repository 에 넘긴다 (아래 마스터 절. 그 입구는 2026-09-29 BL-014 전에
               `finance/db.py` 였다)
재무           입구 `finance/db.py` 가 없다 (2026-09-29 재구성 BL-014) — 조회는 readmodel 이
               조회 연결을, 실행이력 쓰기는 service 가 연결 하나 · 트랜잭션 하나를 빌려
               repository 에 넘긴다 (아래 재무 절)
영업           입구 `sales/db.py` 가 없다 (2026-09-29 BL-013) — 조회는 readmodel 이 조회 연결을,
               쓰기는 service 가 연결 하나 · 트랜잭션 하나를 빌려 repository 에 넘긴다
               (아래 판매 절)
ML             입구 `ml/db.py` 가 없다 (2026-09-29 BL-017) — 원본 창고 풀은 `core/db.py`,
               조회는 readmodel 이 창고에 맞는 풀에서, 적재는 service 가 명시 commit 한 번
               (아래 ML 절)
매입           입구 `purchase_agent/db.py` 가 없다 (2026-09-29 BL-016) — 시세 조회가 조회 연결만
               빌리고 SQL 은 repository 가 받은 연결로 실행한다. 쓰기 대여 없음 (아래 매입 절)
물류           입구 `logistics/db.py` 가 없다 — 조회는 readmodel 이 조회 연결을 빌려
               repository 에 넘긴다. 쓰기는 마스터가 넘긴 연결로만 해서 물류가 스스로 쓰기
               연결을 빌리는 곳은 없다. 스키마 이름을 읽을 때 .env 는 프로세스에서 한 번만
               적재 (아래 물류 절)
```

★ 연결은 `service_pool` fixture 가 바꿔 끼운 **진짜 psycopg_pool 풀 + 가짜 연결 종류**다
  (`tests/core/conftest.py`). 종전 «부서 읽기 범위» 검사는 없앴다 — 그 범위가 하던 연결
  재사용을 이제 풀이 하고, 그것은 `test_core_db.py` 가 잰다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from fake_pg_connection import FakeCursor, FakePgConnection
from psycopg.rows import dict_row

from app.core import db as core_db
from app.core import settings
from app.logistics.repository import rows as logistics_rows

# ── 마스터 — 입구 대신 계층이 빌린다 (2026-09-30 재구성 BL-018) ─────────────────────────
#
# 옛 `master/db.py` 입구(`fetch_one` · `fetch_all` · `execute_returning_one`)에서 재던 것 — 조회는
# 조회
# 연결을 빌려 돌려주고 commit 하지 않는다 · 실패 뒤에도 연결을 돌려주고 오류를 그대로 올린다 ·
# RETURNING 쓰기는 남의 연결에 얹히지 않는 한 트랜잭션이다 · 행이 없으면 종전 문구로 되돌린다 ·
# 쓰기 실패는 되돌린다 — 를 그 일을 맡은 마스터 readmodel(실행 이력 조회) · service(실행 이력
# 저장)에서 같은 진짜 풀로 잰다. 입구가 되내보내던 `get_db_schema` 는 없다 — 스키마 이름이 실제
# SQL 에 실리는지를 조회 검사에서 본다.


def _read_master_runs() -> object:
    from app.master.readmodel.runs import list_runs

    return list_runs(limit=1)


def _read_master_latest_run() -> object:
    from app.master.readmodel.runs import get_run_by_request_id

    return get_run_by_request_id("REQ-20260105-0001", cycle="PROCUREMENT")


def _save_master_run() -> object:
    from app.master.service.run_history import save_run

    return save_run(
        cycle="PROCUREMENT", as_of=date(2026, 1, 5), request_payload={}, response_payload={}
    )


def test_master_reads_borrow_a_read_connection_from_the_shared_pool_and_return_it(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    fake_pg.one = {"a": 1}
    fake_pg.rows = [{"a": 1}]

    assert _read_master_runs() == [{"a": 1}]
    assert _read_master_latest_run() == {"a": 1}

    (conn,) = fake_pg.made  # 두 조회가 한 연결을 다시 썼다
    assert len(conn.executed) == 2
    for query, _params in conn.executed:  # 스키마 이름은 DB_SCHEMA 에서
        assert "haetdeul_test" in query.as_string(None)
    assert "commit" not in conn.events  # 조회는 트랜잭션을 열지 않는다
    assert conn.autocommit is False  # 돌려받은 연결은 쓰기용으로 되돌려져 있다


def test_master_failed_read_returns_the_connection_and_keeps_the_error(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    with core_db.connection() as warm:
        warm.fail_on_execute = psycopg.errors.UndefinedTable("no table")

    with pytest.raises(psycopg.errors.UndefinedTable):
        _read_master_runs()

    (conn,) = fake_pg.made
    conn.fail_on_execute = None
    assert _read_master_runs() == []  # 다음 조회가 같은 연결로 멀쩡히 돈다


def test_master_run_history_write_is_one_transaction_on_its_own_connection(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    """쓰기는 남이 쥔 연결에 얹히지 않는다 — 얹히면 커밋 시점이 바뀐다."""
    fake_pg.one = {"run_id": "X"}

    with core_db.connection() as held:
        _ = held.cursor().execute("SELECT held")
        assert _save_master_run() == {"run_id": "X"}

    write = next(c for c in fake_pg.made if c is not held)
    assert len(write.executed) == 1
    assert "INSERT INTO" in write.executed[0][0].as_string(None)
    assert write.events[-1] == "commit"
    assert "commit" not in held.events


def test_master_run_history_write_without_a_row_rolls_back(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    """RETURNING 행이 없으면 종전 문구로 멈추고, 그 예외로 쓰기가 rollback 된다."""
    with pytest.raises(RuntimeError) as raised:
        _save_master_run()

    assert str(raised.value) == "Database write did not return a row"
    (conn,) = fake_pg.made
    assert conn.events[-1] == "rollback"
    assert "commit" not in conn.events


def test_master_run_history_write_failure_rolls_back_and_keeps_the_error(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    with core_db.connection() as warm:
        warm.fail_on_execute = psycopg.errors.UniqueViolation("dup")

    with pytest.raises(psycopg.errors.UniqueViolation):
        _save_master_run()

    (conn,) = fake_pg.made
    assert conn.events[-1] == "rollback"
    assert "commit" not in conn.events


def test_every_department_shares_the_one_service_pool(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    """같은 접속 대상 · 같은 계정이라 부서별 풀을 따로 만들지 않는다."""
    _read_master_runs()  # 마스터 실행 이력 조회 (2026-09-30 BL-018 전에는 `master/db.py` 입구)
    _read_quotes()  # 매입 시세 조회 (2026-09-29 BL-016 전에는 `purchase_agent/db.py` 입구)
    for made in fake_pg.made:
        made.rows = _logistics_policy_rows()  # 물류 정책 조회는 필수 정책 행을 요구한다
    _read_logistics_policy()  # 물류 활성 정책 조회 (화면 재고 콘솔 · STATUS_QUERY 가 쓴다)

    assert len(fake_pg.connects) == 1


def test_missing_env_message_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, fake_pg: type[FakePgConnection]
) -> None:
    """기준선 실패 21건의 원인 문구가 이것이다 — 바뀌면 원인 대조가 깨진다.

    ★ 2026-09-30 재구성 BL-018: 마스터 실행 이력 조회로 잰다. 그 조회는 종전 입구처럼 스키마
      이름을 먼저 읽으므로(문장 짓기 → 연결 대여) 스키마 이름은 주고 접속 정보만 뺀다.
    """
    for key in settings.DB_CONNECTION_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DB_SCHEMA", "haetdeul_test")
    pool = core_db.DatabasePool("t-missing", settings.database_settings, connection_class=fake_pg)
    monkeypatch.setattr(core_db, "SERVICE_POOL", pool)

    with pytest.raises(RuntimeError) as raised:
        _read_master_runs()

    assert str(raised.value) == (
        "Missing required database environment variables: "
        "DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD"
    )


def test_schema_comes_from_db_schema(db_env: dict[str, str]) -> None:
    """★ 2026-09-30 재구성 BL-018: 마스터 입구의 재수출(`master_db.get_db_schema`)은 없앴다 —
    마스터는 `app.core.settings.get_db_schema` 를 직접 읽고, 그 이름이 SQL 에 실리는지는 위 마스터
    조회 검사가 잰다. 물류는 자기 한 번 적재 입구(`logistics_rows.get_db_schema`)가 그대로다."""
    assert logistics_rows.get_db_schema() == "haetdeul_test"


def test_logistics_still_loads_env_once_while_the_shared_loader_reads_every_call(
    monkeypatch: pytest.MonkeyPatch, db_env: dict[str, str]
) -> None:
    """물류 스키마 이름은 `.env` 를 프로세스에서 한 번만 적재한다 — 공용 적재는 부를 때마다다.

    ★ 2026-09-30 재구성 BL-015: 종전에는 `logistics/db.py` 가 자기 `load_dotenv` 를 따로 들고
      있었다. 이제 한 번 적재 함수(`settings.load_env_file_once`)도 `app/core/settings.py` 에 있어
      같은 기록기로 두 적재 방식을 견준다.
    """
    loads: list[object] = []
    monkeypatch.setattr(settings, "load_dotenv", lambda path: loads.append(path))
    monkeypatch.setattr(settings, "_env_file_loaded_once", False)

    for _ in range(3):
        logistics_rows.get_db_schema()
    assert loads == [settings.ENV_FILE]

    settings.get_db_schema()
    settings.get_db_schema()
    assert loads == [settings.ENV_FILE] * 3


def test_the_pool_reads_connection_settings_once_not_per_borrow(
    monkeypatch: pytest.MonkeyPatch,
    service_pool: core_db.DatabasePool,
    fake_pg: type[FakePgConnection],
) -> None:
    """종전에는 연결마다 `.env` 를 읽었다(물류는 대시보드 한 번에 550회). 이제 풀을 열 때 한 번.

    ★ 2026-09-30 재구성 BL-018: 마스터는 입구 대신 실행 이력 조회로 잰다. 그 조회가 문장을 지으며
      스키마 이름을 읽는
      적재(호출마다 — 공용 적재의 기본)는 이 검사가 재는 것이 아니라 고정값으로 준다 — 종전 입구
      검사도 문장 없이 `"Q"` 를 넘겨 스키마를 안 읽었다.
    """
    loads: list[object] = []
    monkeypatch.setattr(settings, "load_dotenv", lambda path: loads.append(path))
    monkeypatch.setattr("app.master.readmodel.runs.get_db_schema", lambda: "haetdeul_test")
    fake_pg.rows = _logistics_policy_rows()

    for _ in range(3):
        _read_master_runs()
        _read_logistics_policy()

    # 접속 정보 1 + 풀 크기 1 + 연결 상태 확인 1(2026-10-01 BL-010) — 대여마다 늘지 않는다
    assert loads == [settings.ENV_FILE] * 3


# ── ML — 입구 대신 계층이 빌린다 (2026-09-29 BL-017) ──────────────────────────────
#
# 옛 `ml/db.py` 입구에서 재던 것 — 서비스 창고 조회는 공용 서비스 풀의 조회 연결 · 원본 창고는
# 따로 된 풀(`ML_SOURCE_DB_*`) · 적재는 명시 commit 한 번 · 빈 적재는 연결을 빌리지 않음 · 접속
# 정보 누락 문구 — 를 그 일을 맡은 core 풀 설정과 ML readmodel · service 에서 같은 진짜 풀로 잰다.


@pytest.fixture
def source_pool(
    monkeypatch: pytest.MonkeyPatch, fake_pg: type[FakePgConnection]
) -> Any:
    pool = core_db.DatabasePool(
        "test-ml-source", settings.ml_source_database_settings, connection_class=fake_pg
    )
    monkeypatch.setattr(core_db, "ML_SOURCE_POOL", pool)
    yield pool
    pool.close()


def test_ml_source_settings_inherit_db_env_and_swap_the_database(
    monkeypatch: pytest.MonkeyPatch, db_env: dict[str, str]
) -> None:
    monkeypatch.setenv("ML_SOURCE_DB_NAME", " raw_db ")

    assert settings.ml_source_database_settings() == settings.DatabaseSettings(
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

    assert settings.ml_source_database_settings() == settings.DatabaseSettings(
        host="raw.host", port="6543", name="raw_db", user="raw_user", password="raw_pw"
    )


def test_ml_source_requires_a_database_name(
    db_env: dict[str, str], source_pool: core_db.DatabasePool
) -> None:
    from app.ml.readmodel import qa_reads

    with pytest.raises(RuntimeError, match="ML_SOURCE_DB_NAME 이 필요합니다"):
        qa_reads.batch_run(date(2026, 9, 15))
    assert not source_pool.is_open


def test_ml_service_read_borrows_one_read_connection_from_the_shared_pool(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    """ML 서비스 창고 조회도 부서 공용 서비스 풀의 조회 연결 — 따로 풀을 만들지 않는다."""
    from app.ml.readmodel import qa_reads

    fake_pg.one = {"base_dt": date(2026, 9, 14)}
    _read_master_runs()

    assert qa_reads.latest_base_date(date(2026, 9, 15)) == date(2026, 9, 14)

    assert len(fake_pg.connects) == 1  # 마스터 조회와 같은 풀 · 같은 연결
    (conn,) = fake_pg.made
    query, params = conn.executed[-1]
    assert "haetdeul_test.ml_price_forecasts" in query  # 스키마 이름은 DB_SCHEMA 에서
    assert params == (date(2026, 9, 15), date(2026, 9, 15))
    assert "commit" not in conn.events  # 조회는 트랜잭션을 열지 않는다
    assert conn.autocommit is False  # 돌려받은 연결은 쓰기용으로 되돌려져 있다


def test_ml_failed_read_returns_the_connection_and_keeps_the_error(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    from app.ml.readmodel import qa_reads

    with core_db.connection() as warm:
        warm.fail_on_execute = psycopg.errors.UndefinedTable("no table")

    with pytest.raises(psycopg.errors.UndefinedTable):
        qa_reads.usability("배추", "AUC")

    (conn,) = fake_pg.made
    conn.fail_on_execute = None
    assert qa_reads.usability("배추", "AUC") is None  # 다음 조회가 같은 연결로 멀쩡히 돈다


def test_ml_reads_nothing_for_an_empty_target_list(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    """물을 대상일이 없으면(「오늘」만 물음) 전달표 조회 연결을 빌리지 않는다 — 종전 그대로."""
    from app.ml.readmodel import qa_reads

    assert qa_reads.forecast_rows("배추", "AUC", date(2026, 9, 14), []) == []
    assert fake_pg.made == []
    assert not service_pool.is_open


def test_ml_missing_env_message_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, fake_pg: type[FakePgConnection]
) -> None:
    """기준선 실패 21건의 원인 문구가 이것이다 — ML 조회도 같은 문구로 멈춘다."""
    from app.ml.readmodel import qa_reads

    for key in settings.DB_CONNECTION_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    pool = core_db.DatabasePool("t-missing", settings.database_settings, connection_class=fake_pg)
    monkeypatch.setattr(core_db, "SERVICE_POOL", pool)

    with pytest.raises(RuntimeError) as raised:
        qa_reads.latest_base_date()

    assert str(raised.value) == (
        "Missing required database environment variables: "
        "DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD"
    )


def test_ml_keeps_service_and_source_warehouses_in_separate_pools(
    monkeypatch: pytest.MonkeyPatch,
    service_pool: core_db.DatabasePool,
    source_pool: core_db.DatabasePool,
    fake_pg: type[FakePgConnection],
) -> None:
    """🔴 섞으면 서비스 질의가 원본 창고로 가서 «표가 없다» 가 된다."""
    from app.ml.readmodel import forecast_tab, qa_reads

    monkeypatch.setenv("ML_SOURCE_DB_NAME", "raw_db")
    on = date(2026, 9, 15)

    qa_reads.batch_run(on)                        # 원본 — batch_run
    qa_reads.latest_base_date(on)                 # 서비스 — ml_price_forecasts
    qa_reads.agent_report("claude_check", on)     # 원본 — agent_report
    qa_reads.usability("배추", "AUC")             # 서비스 — ml_price_forecasts
    forecast_tab.quality_rows()                   # 원본 — 화면 예측 탭

    by_db = {c.kwargs["dbname"]: c for c in fake_pg.made}
    assert set(by_db) == {"service_db", "raw_db"}
    service_sql = [query for query, _ in by_db["service_db"].executed]
    source_sql = [query for query, _ in by_db["raw_db"].executed]
    assert len(service_sql) == 2 and all("ml_price_forecasts" in q for q in service_sql)
    assert len(source_sql) == 3
    assert "FROM batch_run" in source_sql[0]
    assert "FROM agent_report" in source_sql[1]
    assert "FROM ref_prediction_quality" in source_sql[2]
    assert by_db["raw_db"].kwargs["row_factory"] is dict_row


class _ModelTablesCursor(FakeCursor):
    """현재 모델 조회용 — 교체 이력 표는 상태대로, 모델 행은 물은 이름 그대로 돌려준다."""

    def execute(self, query: Any, params: Any = None) -> None:
        super().execute(query, params)
        if "model_cutover" in str(query):
            if self.connection.cutover == "absent":
                raise psycopg.errors.UndefinedTable("relation model_cutover does not exist")
            if self.connection.cutover == "error":
                raise psycopg.errors.QueryCanceled("canceled")
            self.connection.rows = [{"kind": "rtl", "new_train_end": date(2025, 12, 31),
                                     "swapped_at": None, "note": "n", "time_known": False}]
            return
        (model_ver,) = params
        self.connection.one = {"model_ver": model_ver, "base_dt": date(2026, 9, 14),
                               "model_created_at": date(2026, 9, 12)}


class _ModelTables(FakePgConnection):
    cutover = "absent"

    def cursor(self) -> FakeCursor:
        return _ModelTablesCursor(self)


@pytest.mark.parametrize(
    ("cutover", "read", "rtl_train_end"),
    [("absent", "absent", None), ("error", "error", None), ("ok", "ok", date(2025, 12, 31))],
)
def test_ml_current_models_read_the_source_warehouse_on_one_connection(
    monkeypatch: pytest.MonkeyPatch, db_env: dict[str, str], cutover, read, rtl_train_end
) -> None:
    """교체 이력 표가 없으면 «absent» · 읽다 터지면 «error» 로 적고 **모델 행은 그대로 읽는다.**

    ★ 2026-09-29 BL-017 부터 SQL 넷(교체 이력 1 · 모델 3)이 원본 창고 연결 하나를 쓴다(종전 넷).
      조회 연결은 문장마다 끝나므로(autocommit) 앞 문장의 실패가 뒤 문장을 막지 않는다.
    """
    from app.ml.readmodel import qa_reads

    monkeypatch.setenv("ML_SOURCE_DB_NAME", "raw_db")
    _ModelTables.reset()
    monkeypatch.setattr(_ModelTables, "cutover", cutover)
    pool = core_db.DatabasePool(
        "test-ml-source", settings.ml_source_database_settings, connection_class=_ModelTables
    )
    monkeypatch.setattr(core_db, "ML_SOURCE_POOL", pool)
    try:
        found = qa_reads.current_models()
    finally:
        pool.close()

    assert found["cutover_read"] == read
    assert [m["model_ver"] for m in found["models"]] == ["ops_auc", "ops_whsl", "ops_rtl"]
    assert all(m["created_at"] == date(2026, 9, 12) for m in found["models"])
    assert found["models"][2]["train_end"] == rtl_train_end
    (conn,) = _ModelTables.made
    assert conn.kwargs["dbname"] == "raw_db"
    assert len(conn.executed) == 4
    _ModelTables.reset()


def _source_prediction(offset: int) -> dict[str, Any]:
    """원본 창고 `prediction_log` 한 행 (적재가 읽는 칸만)."""
    base = date(2026, 9, 14)
    return {
        "base_dt": base,
        "target_dt": date.fromordinal(base.toordinal() + offset),
        "item_nm": "배추",
        "lead_biz_d": offset,
        "target_kind": "auc",
        "unit": "원/kg",
        "anchor_prc": 900,
        "pred_prc": 950.4,
        "pred_lo": 800,
        "pred_hi": 1100,
        "gated": False,
        "gate_reason": None,
        "model_ver": "ops_auc",
        "model_created_at": None,
        "use_recommended": True,
        "quality_note": None,
    }


def test_ml_push_loads_in_one_explicit_commit_on_the_service_warehouse(
    monkeypatch: pytest.MonkeyPatch,
    service_pool: core_db.DatabasePool,
    source_pool: core_db.DatabasePool,
    fake_pg: type[FakePgConnection],
) -> None:
    """원본 창고에서 읽고 → 서비스 창고 적재 한 번 = commit 한 번 (종전 `execute_many` 와 같다)."""
    from app.ml.service.forecasts import push_forecasts

    monkeypatch.setenv("ML_SOURCE_DB_NAME", "raw_db")
    fake_pg.rows = [_source_prediction(1), _source_prediction(3)]

    result = push_forecasts(base_dt=date(2026, 9, 14))

    by_db = {c.kwargs["dbname"]: c for c in fake_pg.made}
    source, service = by_db["raw_db"], by_db["service_db"]
    assert len(source.executed) == 1 and "FROM prediction_log" in source.executed[0][0]
    assert "commit" not in source.events
    # 명시 commit 한 번 — 반환은 commit 을 더하지 않는다
    assert service.events == ["check", "executemany", "commit"]
    query, rows = service.executed[0]
    assert "INSERT INTO haetdeul_test.ml_price_forecasts" in query
    #   D+1 · D+3 두 개장일 → 달력일 D+1~D+18 (나머지 16칸은 직전 개장일 값으로 채움)
    assert result["loaded_rows"] == len(rows) == 18
    assert result["filled_rows"] == 16


def test_ml_push_without_calendar_rows_borrows_no_service_connection(
    monkeypatch: pytest.MonkeyPatch,
    service_pool: core_db.DatabasePool,
    source_pool: core_db.DatabasePool,
    fake_pg: type[FakePgConnection],
) -> None:
    """빈 적재는 서비스 창고 연결을 빌리지 않는다. 스키마 이름은 종전처럼 **먼저** 읽는다."""
    from app.ml.service.forecasts import push_forecasts

    monkeypatch.setenv("ML_SOURCE_DB_NAME", "raw_db")
    fake_pg.rows = [_source_prediction(30)]  # 18 달력일 밖 — 적재할 칸이 없다

    assert push_forecasts(base_dt=date(2026, 9, 14))["loaded_rows"] == 0
    assert {c.kwargs["dbname"] for c in fake_pg.made} == {"raw_db"}

    monkeypatch.delenv("DB_SCHEMA")
    with pytest.raises(RuntimeError, match="DB_SCHEMA"):
        push_forecasts(base_dt=date(2026, 9, 14))
    assert not service_pool.is_open


# ── 매입 — 입구 대신 시세 조회가 빌린다 (2026-09-29 BL-016) ──────────────────────────
#
# 옛 `purchase_agent/db.py` 입구에서 재던 것 — 조회는 공용 서비스 풀의 조회 연결을 빌려 돌려주고
# commit 하지 않는다 · 실패 뒤에도 연결을 돌려주고 오류를 그대로 올린다 · 접속 정보 누락 문구 ·
# 쓰기 대여를 들이지 않는다(규칙 2) — 를 그 일을 맡은 시세 조회(`readmodel/quotes.py` → SQL 은
# `repository/quotes.py`)에서 같은 진짜 풀로 잰다.

_QUOTE_ROW = {
    "observed_at": "2026-09-10", "grade": "특", "amount_krw": 1_650_000, "volume_kg": 1_000,
    "market_last_open": "2026-09-10", "trading_days_behind": 0,
}


def _read_quotes() -> list[dict[str, Any]]:
    from app.purchase_agent.readmodel.quotes import auction_quote_source

    return auction_quote_source()("배추", date(2026, 9, 11))


def test_purchase_quote_read_borrows_one_read_connection_and_returns_it(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    fake_pg.rows = [_QUOTE_ROW]

    quotes = _read_quotes()
    _read_quotes()

    assert [(q["grade"], q["price"]) for q in quotes] == [("특", 1650)]
    (conn,) = fake_pg.made  # 두 조회가 한 연결을 다시 썼다
    assert len(conn.executed) == 2
    query, params = conn.executed[0]
    assert "auction_prices_daily" in query.as_string(None)  # 스키마 · 표는 선언에서
    assert params["item"] == "배추" and params["as_of"] == date(2026, 9, 11)
    assert "commit" not in conn.events  # 조회는 트랜잭션을 열지 않는다
    assert conn.autocommit is False  # 돌려받은 연결은 쓰기용으로 되돌려져 있다


def test_purchase_failed_quote_read_returns_the_connection_and_keeps_the_error(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    with core_db.connection() as warm:
        warm.fail_on_execute = psycopg.errors.UndefinedTable("no table")

    with pytest.raises(psycopg.errors.UndefinedTable):
        _read_quotes()

    (conn,) = fake_pg.made
    conn.fail_on_execute = None
    assert _read_quotes() == []  # 다음 조회가 같은 연결로 멀쩡히 돈다


def test_purchase_missing_env_message_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, fake_pg: type[FakePgConnection]
) -> None:
    """기준선 실패 21건의 원인 문구가 이것이다 — 매입 시세 조회도 같은 문구로 멈춘다."""
    for key in settings.DB_CONNECTION_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    pool = core_db.DatabasePool("t-missing", settings.database_settings, connection_class=fake_pg)
    monkeypatch.setattr(core_db, "SERVICE_POOL", pool)

    with pytest.raises(RuntimeError) as raised:
        _read_quotes()

    assert str(raised.value) == (
        "Missing required database environment variables: "
        "DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD"
    )


def test_purchase_borrows_only_read_connections() -> None:
    """매입은 read-only(규칙 2). 패키지 어디서도 연결 모듈을 통째로 들이거나 쓰기 대여 ·
    경계 · 스키마 헬퍼를 들이지 않는다 — 조회 대여와 타입 이름뿐이다."""
    import ast
    from pathlib import Path

    import app.purchase_agent as purchase

    allowed = {"read_connection", "Connection", "Params", "Query"}
    offenders = []
    for path in sorted(Path(purchase.__file__).parent.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders += [f"{path.name}: import {a.name}" for a in node.names
                              if a.name.startswith("app.core.db")]
            elif isinstance(node, ast.ImportFrom) and node.module == "app.core":
                offenders += [f"{path.name}: from app.core import {a.name}" for a in node.names
                              if a.name == "db"]
            elif isinstance(node, ast.ImportFrom) and node.module == "app.core.db":
                offenders += [f"{path.name}: {a.name}" for a in node.names
                              if a.name not in allowed]
            elif isinstance(node, ast.ImportFrom) and node.module == "app.core.settings":
                offenders += [f"{path.name}: {a.name}" for a in node.names
                              if a.name == "get_db_schema"]
    assert offenders == []


# ── 재무 — 입구 대신 계층이 빌린다 (2026-09-29 재구성 BL-014) ─────────────────────────
#
# 옛 `finance/db.py` 입구에서 재던 것 — 조회는 조회 연결을 빌려 돌려주고 commit 하지 않는다 ·
# 쓰기는 남의 연결에 얹히지 않는 한 트랜잭션이다 · 행이 없으면 되돌린다 — 을 그 일을 맡은 재무
# readmodel · service 에서 같은 진짜 풀로 잰다. 헬퍼 몸통은 마스터 입구(`app/master/db.py`)로
# 옮겨 위 입구 검사가 그대로 잰다 — 2026-09-30 BL-018 에 그 입구도 없어져 위 마스터 절이 마스터
# readmodel · service 에서 잰다.

_FINANCE_STATE_ROW = {
    "finance_state_id": "FIN-DAY-SIM-1-LOAN_BASELINE-20260105",
    "sim_run_id": "SIM-1",
    "state_date": date(2026, 1, 5),
    "state_type": "DAY",
    "financing_mode": "LOAN_BASELINE",
    "current_cash_krw": Decimal(53_952_691),
    "minimum_operating_cash_krw": Decimal(15_902_640),
    "committed_outflows_krw": Decimal(0),
    "unsettled_purchase_payables_krw": Decimal(0),
    "receivables_krw": Decimal(0),
    "current_debt_krw": Decimal(0),
    "financial_limit_krw": Decimal(38_050_051),
}


def _save_finance_run() -> object:
    from app.finance.service.run_history import save_finance_agent_run

    return save_finance_agent_run(
        cycle="PROCUREMENT",
        as_of=date(2026, 1, 5),
        snapshot_id=None,
        runtime_status="RUNTIME_NOT_READY",
        verdict=None,
        request_payload={},
        response_payload={"verdict": None},
    )


def test_finance_read_borrows_one_read_connection_and_returns_it(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    from app.finance.readmodel.finance_state import load_finance_state_row

    fake_pg.rows = [dict(_FINANCE_STATE_ROW)]

    row = load_finance_state_row(date(2026, 1, 5), sim_run_id="SIM-1")

    assert row["finance_state_id"] == _FINANCE_STATE_ROW["finance_state_id"]
    (conn,) = fake_pg.made  # 축 조회와 상태 조회가 한 연결을 썼다
    assert len(conn.executed) == 2
    assert "commit" not in conn.events  # 조회는 트랜잭션을 열지 않는다
    assert conn.autocommit is False  # 돌려받은 연결은 쓰기용으로 되돌려져 있다


def test_finance_failed_read_returns_the_connection_and_keeps_the_error(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    from app.finance.readmodel.finance_state import get_finance_runtime_axis

    with core_db.connection() as warm:
        warm.fail_on_execute = psycopg.errors.UndefinedTable("no table")

    with pytest.raises(psycopg.errors.UndefinedTable):
        get_finance_runtime_axis(sim_run_id="SIM-1")

    (conn,) = fake_pg.made
    conn.fail_on_execute = None
    conn.rows = [dict(_FINANCE_STATE_ROW)]
    assert get_finance_runtime_axis(sim_run_id="SIM-1")["sim_run_id"] == "SIM-1"


def test_finance_run_history_write_is_one_transaction_on_its_own_connection(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    """실행이력은 원장 연결에 얹히지 않는다 — 얹히면 원장 rollback 이 이력까지 지운다."""
    fake_pg.one = {"run_id": "RUN-1"}

    with core_db.connection() as held:
        _ = held.cursor().execute("SELECT held")
        assert _save_finance_run() == {"run_id": "RUN-1"}

    write = next(c for c in fake_pg.made if c is not held)
    assert len(write.executed) == 1
    assert write.events[-1] == "commit"
    assert "commit" not in held.events


def test_finance_run_history_write_without_a_row_rolls_back(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    """RETURNING 행이 없으면 종전 문구로 멈추고, 그 예외로 쓰기가 rollback 된다."""
    with pytest.raises(RuntimeError) as raised:
        _save_finance_run()

    assert str(raised.value) == "Database write did not return a row"
    (conn,) = fake_pg.made
    assert conn.events[-1] == "rollback"
    assert "commit" not in conn.events


def test_finance_run_history_write_failure_rolls_back_and_keeps_the_error(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    with core_db.connection() as warm:
        warm.fail_on_execute = psycopg.errors.UniqueViolation("dup")

    with pytest.raises(psycopg.errors.UniqueViolation):
        _save_finance_run()

    (conn,) = fake_pg.made
    assert conn.events[-1] == "rollback"
    assert "commit" not in conn.events


# ── 물류 — 입구 대신 계층이 빌린다 ─────────────────────────────────────────────────
#
# 옛 `logistics/db.py` 입구에서 재던 것 중 지금도 물류가 하는 일 — 조회는 조회 연결을 빌려
# 돌려주고 commit 하지 않는다 · 실패 뒤에도 연결을 돌려주고 오류를 그대로 올린다 · 접속 정보 누락
# 문구 — 을 그 일을 맡은 물류 readmodel(활성 정책 조회)에서 같은 진짜 풀로 잰다. 물류 쓰기는
# 마스터가 넘긴 연결로만 해서 물류가 스스로 쓰기 연결을 빌리는 경계가 없다.


def _logistics_policy_rows() -> list[dict[str, object]]:
    from app.logistics.schemas.snapshot import LOGISTICS_POLICY_VERSION
    from app.logistics.schemas.vocabulary import USAGE_SCOPE

    values = {
        "guaranteed_capacity_kg": ("NUMERIC", Decimal(8000)),
        "burst_capacity_kg": ("NUMERIC", Decimal(9600)),
        "inbound_lead_days": ("NUMERIC", Decimal(2)),
        "daily_inbound_capacity_kg": ("NUMERIC", Decimal(5000)),
        "inbound_transport_capacity_kg": ("NUMERIC", Decimal(5000)),
        "shared_daily_outbound_capacity_kg": ("NUMERIC", Decimal(5000)),
        "cap_by_date_policy": ("TEXT", "CONFIRMED_ONLY"),
    }
    return [
        {
            "policy_key": key,
            "value_kind": kind,
            "value_numeric": value if kind == "NUMERIC" else None,
            "value_text": value if kind == "TEXT" else None,
            "value_json": None,
            "source_ref": f"MVP-POLICY:{key}",
            "policy_version": LOGISTICS_POLICY_VERSION,
            "usage_scope": USAGE_SCOPE,
        }
        for key, (kind, value) in values.items()
    ]


def _read_logistics_policy() -> object:
    from app.logistics.readmodel.current import read_active_logistics_policy

    return read_active_logistics_policy()


def test_logistics_read_borrows_one_read_connection_and_returns_it(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    fake_pg.rows = _logistics_policy_rows()

    first = _read_logistics_policy()
    second = _read_logistics_policy()

    assert first.guaranteed_capacity_kg == Decimal(8000)
    assert second == first
    (conn,) = fake_pg.made  # 두 조회가 한 연결을 다시 썼다
    assert len(conn.executed) == 2
    assert "agent_policy_config" in str(conn.executed[0][0])
    assert "commit" not in conn.events  # 조회는 트랜잭션을 열지 않는다
    assert conn.autocommit is False  # 돌려받은 연결은 쓰기용으로 되돌려져 있다


def test_logistics_failed_read_returns_the_connection_and_keeps_the_error(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    with core_db.connection() as warm:
        warm.fail_on_execute = psycopg.errors.UndefinedTable("no table")

    with pytest.raises(psycopg.errors.UndefinedTable):
        _read_logistics_policy()

    (conn,) = fake_pg.made
    conn.fail_on_execute = None
    conn.rows = _logistics_policy_rows()
    # 다음 조회가 같은 연결로 멀쩡히 돈다
    assert _read_logistics_policy().guaranteed_capacity_kg == Decimal(8000)
    assert fake_pg.made == [conn]


def test_logistics_missing_env_message_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, fake_pg: type[FakePgConnection]
) -> None:
    """기준선 실패의 원인 문구가 이것이다 — 입구가 계층으로 옮겨도 같은 문구여야 한다."""
    for key in settings.DB_CONNECTION_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    pool = core_db.DatabasePool("t-missing", settings.database_settings, connection_class=fake_pg)
    monkeypatch.setattr(core_db, "SERVICE_POOL", pool)

    with pytest.raises(RuntimeError) as raised:
        _read_logistics_policy()

    assert str(raised.value) == (
        "Missing required database environment variables: "
        "DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD"
    )


# ── 판매 — 입구 대신 계층이 빌린다 (2026-09-29 BL-013) ─────────────────────────────
#
# 위 입구 검사가 영업 `db.py` 에서 재던 것 — 조회는 조회 연결을 빌려 돌려주고 commit 하지
# 않는다 · 쓰기는 남의 연결에 얹히지 않는 한 트랜잭션이다 · 행이 없으면 되돌린다 — 을 그
# 일을 맡은 판매 readmodel · service 에서 같은 진짜 풀로 잰다.

_PARTNER_ROW = {
    "partner_id": "P-1",
    "partner_name": "새 거래처",
    "partner_type": "CUSTOMER",
    "client_type": None,
    "factory_region": None,
    "factory_city": None,
    "factory_area": None,
    "sales_collection_days": None,
    "pricing_contract_type": None,
    "active": True,
    "provisional": False,
    "note": None,
}


def test_sales_read_borrows_one_read_connection_and_returns_it(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    from datetime import date

    from app.sales.readmodel.dashboard import get_sales_dashboard

    get_sales_dashboard(sim_run_id="SIM-1", as_of=date(2026, 1, 7))

    (conn,) = fake_pg.made  # 조회 일곱 개가 한 연결을 썼다
    assert len(conn.executed) == 7
    assert "commit" not in conn.events  # 조회는 트랜잭션을 열지 않는다
    assert conn.autocommit is False  # 돌려받은 연결은 쓰기용으로 되돌려져 있다


def test_sales_write_is_one_transaction_on_its_own_connection(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    """쓰기는 남이 쥔 연결에 얹히지 않는다 — 얹히면 커밋 시점이 바뀐다."""
    from app.sales.service.partners import create_partner

    fake_pg.one = dict(_PARTNER_ROW)

    with core_db.connection() as held:
        _ = held.cursor().execute("SELECT held")
        profile = create_partner(
            {"partner_id": "P-1", "partner_name": "새 거래처", "partner_type": "CUSTOMER"}
        )

    assert profile.partner_id == "P-1"
    write = next(c for c in fake_pg.made if c is not held)
    assert len(write.executed) == 1
    assert write.events[-1] == "commit"
    assert "commit" not in held.events


def test_sales_write_without_a_row_rolls_back(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    """`RETURNING` 행이 없으면(같은 코드가 있다) 업무 예외로 멈추고, 그 쓰기는 rollback 된다."""
    from app.sales.schemas.partners import PartnerAlreadyExists
    from app.sales.service.partners import create_partner

    with pytest.raises(PartnerAlreadyExists):
        create_partner(
            {"partner_id": "P-1", "partner_name": "새 거래처", "partner_type": "CUSTOMER"}
        )

    (conn,) = fake_pg.made
    assert conn.events[-1] == "rollback"
    assert "commit" not in conn.events


def test_sales_failed_read_returns_the_connection_and_keeps_the_error(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    from app.sales.readmodel.console_items import get_console_items

    with core_db.connection() as warm:
        warm.fail_on_execute = psycopg.errors.UndefinedTable("no table")

    with pytest.raises(psycopg.errors.UndefinedTable):
        get_console_items()

    (conn,) = fake_pg.made
    conn.fail_on_execute = None
    assert get_console_items().rows == []  # 다음 조회가 같은 연결로 멀쩡히 돈다


def test_sales_write_failure_rolls_back_and_keeps_the_error(
    service_pool: core_db.DatabasePool, fake_pg: type[FakePgConnection]
) -> None:
    """🔴 실행 오류는 **업무 예외로 바꾸지 않는다** — «이미 있다» 는 행이 안 나왔을 때뿐이다."""
    from app.sales.service.partners import create_partner

    with core_db.connection() as warm:
        warm.fail_on_execute = psycopg.errors.UniqueViolation("dup")

    with pytest.raises(psycopg.errors.UniqueViolation):
        create_partner(
            {"partner_id": "P-1", "partner_name": "새 거래처", "partner_type": "CUSTOMER"}
        )

    (conn,) = fake_pg.made
    assert conn.events[-1] == "rollback"
    assert "commit" not in conn.events
