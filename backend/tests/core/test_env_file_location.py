"""앱이 읽는 로컬 설정 파일은 `backend/.env` 하나다 (2026-10-03 재구성 BL-030).

위치와 적재 구현은 `app/core/settings.py` 에 있다. 부서 설정 함수(DB · LLM · 매입 기능 플래그 ·
ML 콘솔 주소)가 모두 같은 적재 함수(`settings.load_env_file` · 물류 스키마 이름은
`settings.load_env_file_once`)를 거쳐 같은 파일을 읽는지 잰다. 실제 `.env` 는 읽지 않는다 —
`settings.ENV_FILE` 을 임시 파일로 바꾸고 진짜 `load_dotenv` 로 적재한다. 설정 함수만 부르므로
외부 호출은 없다.

★ 종전 위치: 일반 LLM 은 `backend/.env` · 저장소 루트 `.env`, 재무는 `backend/app/.env` ·
  `backend/.env`, Critic 은 `backend/app/.env` 만 읽었고, ML 콘솔 주소는 `load_dotenv` 를
  직접 불렀다.
"""

from __future__ import annotations

import ast
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import pytest
from dotenv import load_dotenv

import app
from app.core import settings


def _master() -> str:
    from app.master.llm.runtime import get_llm_settings

    return get_llm_settings().model


def _critic() -> str:
    from app.master.critic.llm.runtime import get_llm_settings

    return get_llm_settings().model


def _logistics() -> str:
    from app.logistics.llm.runtime import get_llm_settings

    return get_llm_settings().model


def _purchase() -> str:
    from app.purchase_agent.llm.runtime import get_llm_settings

    return get_llm_settings().model


def _purchase_flag() -> str:
    from app.purchase_agent import features

    return str(features.enabled(features.SELF_REVIEW))


def _sales() -> str:
    from app.sales.llm.runtime import load_settings

    return load_settings().model


def _finance() -> str:
    from app.finance.llm.client import ollama_tool_calling_model

    return ollama_tool_calling_model()


def _ml_interpreter() -> str:
    from app.ml.llm import qa

    return qa._api_key()


def _ml_console() -> str:
    from app.ml import ml_backend

    return ml_backend.console_origin()[0]


def _db_schema() -> str:
    return settings.get_db_schema()


@dataclass(frozen=True)
class Reader:
    read: Callable[[], str]
    key: str
    #: 파일 값과 그때 함수가 내는 값.
    in_file: str
    from_file: str
    #: 주입한 환경변수 값과 그때 함수가 내는 값.
    injected: str
    from_injected: str


_READERS = {
    "master": Reader(_master, "MASTER_LLM_MODEL", "file-m", "file-m", "env-m", "env-m"),
    "critic": Reader(_critic, "CRITIC_LLM_MODEL", "file-m", "file-m", "env-m", "env-m"),
    "logistics": Reader(_logistics, "LOGISTICS_LLM_MODEL", "file-m", "file-m", "env-m", "env-m"),
    "purchase": Reader(_purchase, "PURCHASE_LLM_MODEL", "file-m", "file-m", "env-m", "env-m"),
    "purchase-flag": Reader(
        _purchase_flag, "PURCHASE_LLM_SELF_REVIEW_ENABLED", "true", "True", "false", "False"
    ),
    "sales": Reader(_sales, "SALES_LLM_MODEL", "file-m", "file-m", "env-m", "env-m"),
    "finance": Reader(
        _finance, "FINANCE_OLLAMA_PLANNER_MODEL", "file-m", "file-m", "env-m", "env-m"
    ),
    "ml-interpreter": Reader(
        _ml_interpreter, "ML_GEMINI_API_KEY", "file-k", "file-k", "env-k", "env-k"
    ),
    "ml-console": Reader(
        _ml_console, "ML_CONSOLE_ORIGIN", "http://file.test:1", "http://file.test:1",
        "http://env.test:2", "http://env.test:2",
    ),
    "db-schema": Reader(_db_schema, "DB_SCHEMA", "file_s", "file_s", "env_s", "env_s"),
}

each_reader = pytest.mark.parametrize("reader", list(_READERS.values()), ids=list(_READERS))


@dataclass
class EnvFile:
    path: Path
    #: 적재 함수가 `load_dotenv` 에 넘긴 경로 — 부른 순서대로.
    loads: list[object] = field(default_factory=list)


@pytest.fixture
def environ() -> Iterator[None]:
    """검사 동안의 프로세스 환경. 적재가 넣은 값까지 끝나면 통째로 되돌린다.

    `monkeypatch.delenv` 는 없던 키를 기록하지 않아 적재가 새로 넣은 키가 남는다 — 그래서
    `patch.dict` 로 감싼다. 이 검사가 보는 변수와 공용 LLM 변수는 비우고 시작한다.
    """
    keys = {reader.key for reader in _READERS.values()}
    with patch.dict(os.environ):
        for key in list(os.environ):
            if key.startswith(("LLM_", "GEMINI_")) or key in keys:
                del os.environ[key]
        yield


@pytest.fixture
def env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environ: None) -> EnvFile:
    """`settings.ENV_FILE` 를 임시 파일로 돌리고 진짜 `load_dotenv` 로 적재한다(기록하면서)."""
    path = tmp_path / "backend" / ".env"
    path.parent.mkdir()
    path.write_text(
        "".join(f"{reader.key}={reader.in_file}\n" for reader in _READERS.values()),
        encoding="utf-8",
    )
    recorded = EnvFile(path)

    def recording_load_dotenv(dotenv_path: object) -> bool:
        recorded.loads.append(dotenv_path)
        return load_dotenv(dotenv_path)

    monkeypatch.setattr(settings, "ENV_FILE", path)
    monkeypatch.setattr(settings, "load_dotenv", recording_load_dotenv)
    monkeypatch.setattr(settings, "_env_file_loaded_once", False)
    return recorded


@each_reader
def test_each_reader_takes_the_value_from_the_shared_env_file(env_file, reader) -> None:
    assert reader.read() == reader.from_file
    assert env_file.loads == [env_file.path]


@each_reader
def test_an_injected_environment_variable_wins_over_the_file(env_file, reader) -> None:
    os.environ[reader.key] = reader.injected

    assert reader.read() == reader.from_injected
    assert os.environ[reader.key] == reader.injected
    assert env_file.loads == [env_file.path]


@each_reader
def test_a_missing_file_leaves_the_injected_environment_usable(env_file, reader) -> None:
    env_file.path.unlink()
    os.environ[reader.key] = reader.injected

    assert reader.read() == reader.from_injected
    assert env_file.loads == [env_file.path]


@each_reader
def test_the_working_directory_does_not_move_the_file(
    env_file, tmp_path, monkeypatch, reader
) -> None:
    """실행 디렉터리 · 그 `app/` · 부모에 다른 값의 `.env` 가 있어도 `backend/.env` 만 읽는다."""
    elsewhere = tmp_path / "elsewhere" / "backend"
    (elsewhere / "app").mkdir(parents=True)
    for decoy in (elsewhere / ".env", elsewhere / "app" / ".env", elsewhere.parent / ".env"):
        decoy.write_text(f"{reader.key}=decoy\n", encoding="utf-8")
    monkeypatch.chdir(elsewhere)

    assert reader.read() == reader.from_file
    assert env_file.loads == [env_file.path]


@each_reader
def test_readers_load_the_file_on_every_call(env_file, reader) -> None:
    """부를 때마다 적재한다(종전 시점 그대로). 물류 스키마 이름만 한 번 — 아래 검사."""
    reader.read()
    reader.read()

    assert env_file.loads == [env_file.path, env_file.path]


def test_logistics_schema_name_still_loads_the_file_once(env_file) -> None:
    from app.logistics.repository.rows import get_db_schema

    assert get_db_schema() == "file_s"
    assert get_db_schema() == "file_s"

    assert env_file.loads == [env_file.path]


def test_the_env_file_is_backend_dot_env() -> None:
    backend = Path(app.__file__).resolve().parent.parent
    assert settings.ENV_FILE == backend / ".env"


def test_only_core_settings_imports_dotenv() -> None:
    """파일 적재 구현은 한 자리다 — 부서 · `core/llm` 에 `dotenv` import 가 없다."""
    root = Path(app.__file__).resolve().parent
    found = []
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name == "dotenv" or name.startswith("dotenv.") for name in names):
                found.append(path.relative_to(root).as_posix())
    assert found == ["core/settings.py"]
