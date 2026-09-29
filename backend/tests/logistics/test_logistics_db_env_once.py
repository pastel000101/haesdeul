"""물류 DB 설정 파일(.env)을 프로세스에서 한 번만 읽는지 확인한다 (실 DB 연결 없음).

★ 2026-09-29 풀 전환 뒤 물류 입구가 `.env` 를 읽는 자리는 스키마 이름 하나다. 접속 정보는
  공통 풀이 열릴 때 한 번 읽는다 (`tests/core/test_department_db_entrypoints.py::
  test_the_pool_reads_connection_settings_once_not_per_borrow`).

★ 2026-09-30 재구성 BL-015: 입구 `logistics/db.py` 가 없어졌다. 스키마 이름은
  `repository/rows.get_db_schema` 가, 한 번 적재는 `core/settings.load_env_file_once` 가 맡는다.
"""

import pytest

from app.core import settings
from app.logistics.repository import rows


@pytest.fixture
def load_calls(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """`load_dotenv` 를 기록기로 바꾸고 한 번 읽음 표시를 이 검사 안에서 처음 상태로 되돌린다."""
    calls: list[object] = []
    monkeypatch.setattr(settings, "load_dotenv", lambda path: calls.append(path))
    monkeypatch.setattr(settings, "_env_file_loaded_once", False)
    monkeypatch.setenv("DB_SCHEMA", "logistics_test")
    return calls


def test_여러_번_불러도_설정_파일은_한_번만_읽는다(load_calls: list[object]) -> None:
    for _ in range(5):
        assert rows.get_db_schema() == "logistics_test"

    assert load_calls == [settings.ENV_FILE]


def test_값은_매번_환경변수에서_읽는다(
    load_calls: list[object], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert rows.get_db_schema() == "logistics_test"
    monkeypatch.setenv("DB_SCHEMA", "logistics_other")

    assert rows.get_db_schema() == "logistics_other"
    assert len(load_calls) == 1


def test_빠진_변수는_그대로_멈춘다(
    load_calls: list[object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DB_SCHEMA")

    with pytest.raises(
        RuntimeError, match="Missing required database environment variables: DB_SCHEMA"
    ):
        rows.get_db_schema()
    assert len(load_calls) == 1
