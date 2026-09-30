"""`.sql` 파일 하나를 있는 그대로 적용하는 실행기 (2026-09-11).

🔴 **`psql` 이 이 환경에 없다.** 그렇다고 DDL 을 손으로 옮겨 적으면 **파일과 실제로
   돈 것이 갈리고**, 그때 어느 쪽이 맞는지 답할 방법이 없다.

★★ **왜 이 검사가 필요한가.** 오늘(`2026-09-11`) 걷기가 판매 검증에서 멈췄고 원인이
  **저장소에 있는 마이그레이션이 DB 에 안 들어간 것**이었다. `#353` 때 *"DB 적용 →
  코드 머지"* 로 정한 순서를 어긴 결과이고, 그때는 승인 32건이 죽었다.

🔴 **이 실행기의 안전 속성은 하나다 — `--apply` 가 없으면 DB 에 안 붙는다.**
   그 속성이 깨지면 *"무엇이 도는지 보려고" 부른 명령이 실제로 돈다.*
"""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

import apply_sql


@pytest.fixture
def 붙으면_터지는_psycopg(monkeypatch):
    """🔴 DB 에 붙는 순간 터뜨린다. *"안 붙었다"* 를 값으로 잰다."""

    def 절대_안_됨(*_args, **_kwargs):
        raise AssertionError("DB 에 붙었다 — --apply 없이 붙으면 안 된다")

    monkeypatch.setattr(apply_sql.psycopg, "connect", 절대_안_됨)


def _sql파일(tmp_path: Path) -> Path:
    경로 = tmp_path / "probe.sql"
    경로.write_text("SELECT 1;\n", encoding="utf-8")
    return 경로


def test_apply_없이는_DB_에_안_붙는다(tmp_path, monkeypatch, capsys, 붙으면_터지는_psycopg) -> None:
    """🔴 **이 실행기의 전부다.**"""
    monkeypatch.setattr(sys, "argv", ["apply_sql.py", str(_sql파일(tmp_path))])

    assert apply_sql.main() == 0

    나온것 = unicodedata.normalize("NFC", capsys.readouterr().out)
    assert "SELECT 1;" in 나온것, "무엇이 돌지 안 보여 준다"
    assert "안 돌렸다" in 나온것, "안 돌렸다는 사실을 안 말한다"


def test_dry_run_도_같다(tmp_path, monkeypatch, 붙으면_터지는_psycopg) -> None:
    """⚠️ `--dry-run` 은 **기본과 같은 것**이지 다른 길이 아니다."""
    monkeypatch.setattr(sys, "argv", ["apply_sql.py", str(_sql파일(tmp_path)), "--dry-run"])

    assert apply_sql.main() == 0


def test_없는_파일이면_막는다(tmp_path, monkeypatch, 붙으면_터지는_psycopg) -> None:
    """★ 없는 파일을 빈 문자열로 읽어 **아무것도 안 하고 성공**하면 안 된다."""
    monkeypatch.setattr(sys, "argv", ["apply_sql.py", str(tmp_path / "없다.sql")])

    with pytest.raises(SystemExit) as 터진것:
        apply_sql.main()

    assert "파일이 없다" in unicodedata.normalize("NFC", str(터진것.value))


def test_파일을_한_글자도_안_바꾼다(tmp_path, monkeypatch, capsys, 붙으면_터지는_psycopg) -> None:
    """🔴 **SQL 을 만들지 않는다.** 읽어서 그대로 보낸다.

    ★★ 손으로 옮겨 적거나 문장을 쪼개면 파일과 실제로 돈 것이 갈린다 — 그것이
      이 실행기를 만든 이유 자체를 없앤다.
    """
    본문 = "-- 주석\nBEGIN;\n  ALTER TABLE x ADD CONSTRAINT y CHECK (z IN ('a', 'b'));\nCOMMIT;\n"
    경로 = tmp_path / "ddl.sql"
    경로.write_text(본문, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["apply_sql.py", str(경로)])

    apply_sql.main()

    나온것 = unicodedata.normalize("NFC", capsys.readouterr().out)
    assert 본문.strip() in 나온것, "보여 준 것이 파일과 다르다"


# ══════════════════════════════════════════════════════════════════════════════
# psql 명령 줄이 든 파일 (2026-09-30 · BL-021)
# ══════════════════════════════════════════════════════════════════════════════
#
# pg_dump 스냅샷(`schema/10_domain_schema.sql`)의 `\restrict` · `\unrestrict` 와 이관 판의
# `\i` 는 서버가 SQL 로 받지 못한다. 그런 파일만 psql 로 보내고, 줄을 지우지 않는다.

_DB_ENV = {"DB_HOST": "db.test", "DB_PORT": "5432", "DB_NAME": "empty", "DB_USER": "tester"}
_PASSWORD = "not-a-real-password"


class _Completed:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


@pytest.fixture
def db_env(monkeypatch):
    """접속 정보는 환경변수로만 준다 — `backend/.env` 를 읽지 않게 막는다."""
    monkeypatch.setattr(apply_sql, "load_dotenv", lambda *_a, **_k: False)
    for name, value in _DB_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DB_PASSWORD", _PASSWORD)


def _psql_file(tmp_path: Path) -> Path:
    path = tmp_path / "dump.sql"
    path.write_text("\\restrict KEY\nSELECT 1;\n\\unrestrict KEY\n", encoding="utf-8")
    return path


def _record_psql(monkeypatch, returncode: int = 0) -> list[dict]:
    calls: list[dict] = []

    def fake_run(command, **kwargs):
        calls.append({"command": list(command), **kwargs})
        return _Completed(returncode)

    monkeypatch.setattr(apply_sql.shutil, "which", lambda name: f"/opt/pg/bin/{name}")
    monkeypatch.setattr(apply_sql.subprocess, "run", fake_run)
    return calls


def test_psql_command_file_goes_through_psql_with_on_error_stop(
    tmp_path, monkeypatch, capsys, 붙으면_터지는_psycopg, db_env
) -> None:
    calls = _record_psql(monkeypatch)
    path = _psql_file(tmp_path)
    monkeypatch.setattr(sys, "argv", ["apply_sql.py", str(path), "--apply"])

    assert apply_sql.main() == 0

    (call,) = calls
    assert call["command"] == [
        "/opt/pg/bin/psql", "-X", "-w", "-v", "ON_ERROR_STOP=1", "-f", str(path)
    ]
    assert call["cwd"] == apply_sql._REPO
    env = call["env"]
    assert (env["PGHOST"], env["PGPORT"], env["PGDATABASE"], env["PGUSER"]) == tuple(
        _DB_ENV.values()
    )
    assert env["PGPASSWORD"] == _PASSWORD
    assert env["PGCLIENTENCODING"] == "UTF8"
    assert all(_PASSWORD not in part for part in call["command"])
    # 파일은 그대로 psql 에 넘긴다 — 줄을 지운 사본을 만들지 않는다.
    assert path.read_text(encoding="utf-8").startswith("\\restrict KEY\n")


def test_psql_failure_fails_the_script_with_its_exit_code(
    tmp_path, monkeypatch, capsys, 붙으면_터지는_psycopg, db_env
) -> None:
    _record_psql(monkeypatch, returncode=3)
    monkeypatch.setattr(sys, "argv", ["apply_sql.py", str(_psql_file(tmp_path)), "--apply"])

    assert apply_sql.main() == 3
    assert "psql 이 실패했다" in unicodedata.normalize("NFC", capsys.readouterr().out)


def test_missing_psql_stops_before_anything_runs(
    tmp_path, monkeypatch, 붙으면_터지는_psycopg, db_env
) -> None:
    monkeypatch.setattr(apply_sql.shutil, "which", lambda _name: None)

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("psql 이 없는데 무언가를 돌렸다")

    monkeypatch.setattr(apply_sql.subprocess, "run", must_not_run)
    monkeypatch.setattr(sys, "argv", ["apply_sql.py", str(_psql_file(tmp_path)), "--apply"])

    with pytest.raises(SystemExit) as stopped:
        apply_sql.main()

    assert "psql" in unicodedata.normalize("NFC", str(stopped.value))


def test_plain_sql_still_goes_through_psycopg(tmp_path, monkeypatch, capsys, db_env) -> None:
    sent: list[str] = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, text):
            sent.append(text)

    class FakeConnection(FakeCursor):
        def cursor(self):
            return FakeCursor()

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("psql 명령 줄이 없는 파일을 psql 로 보냈다")

    monkeypatch.setattr(apply_sql.psycopg, "connect", lambda *_a, **_k: FakeConnection())
    monkeypatch.setattr(apply_sql.subprocess, "run", must_not_run)
    path = _sql파일(tmp_path)
    monkeypatch.setattr(sys, "argv", ["apply_sql.py", str(path), "--apply"])

    assert apply_sql.main() == 0
    assert sent == [path.read_text(encoding="utf-8")]


def test_dry_run_names_the_runner_and_the_psql_lines(
    tmp_path, monkeypatch, capsys, 붙으면_터지는_psycopg
) -> None:
    def must_not_run(*_args, **_kwargs):
        raise AssertionError("--apply 없이 psql 을 돌렸다")

    monkeypatch.setattr(apply_sql.subprocess, "run", must_not_run)
    monkeypatch.setattr(sys, "argv", ["apply_sql.py", str(_psql_file(tmp_path))])

    assert apply_sql.main() == 0

    out = unicodedata.normalize("NFC", capsys.readouterr().out)
    assert "실행   psql" in out
    assert "1행 \\restrict · 3행 \\unrestrict" in out
    assert "안 돌렸다" in out


# ══════════════════════════════════════════════════════════════════════════════
# 새 DB 구축 — `--new-database` (2026-09-30 · BL-021)
# ══════════════════════════════════════════════════════════════════════════════


def _order_file(tmp_path: Path, monkeypatch, names: list[str], *, listed=None) -> list[Path]:
    """임시 저장소에 SQL 파일과 적용 목록을 만들고 스크립트가 그 자리를 보게 한다."""
    paths = []
    for name in names:
        path = tmp_path / "database" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"SELECT '{name}';\n", encoding="utf-8")
        paths.append(path)
    order = tmp_path / "database" / "new_database_order.txt"
    entries = listed if listed is not None else [f"database/{name}" for name in names]
    order.write_text("# 머리말\n\n" + "\n".join(entries) + "\n", encoding="utf-8")
    monkeypatch.setattr(apply_sql, "_REPO", tmp_path)
    monkeypatch.setattr(apply_sql, "_NEW_DATABASE_ORDER", order)
    return paths


def _record_applies(monkeypatch, fail_at: int | None = None) -> list[str]:
    applied: list[str] = []

    def fake_psycopg(text):
        applied.append(text.strip())
        return 3 if fail_at is not None and len(applied) == fail_at else 0

    monkeypatch.setattr(apply_sql, "_apply_with_psycopg", fake_psycopg)
    monkeypatch.setattr(apply_sql, "_haetdeul_object_count", lambda: 0)
    return applied


def test_new_database_applies_the_listed_files_in_list_order(
    tmp_path, monkeypatch, capsys, db_env
) -> None:
    # 파일 이름 정렬(a → b → c)과 다른 순서로 적는다 — 목록이 순서다.
    _order_file(tmp_path, monkeypatch, ["schema/c.sql", "schema/a.sql", "migrations/b.sql"])
    applied = _record_applies(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["apply_sql.py", "--new-database", "--apply"])

    assert apply_sql.main() == 0
    assert applied == [
        "SELECT 'schema/c.sql';",
        "SELECT 'schema/a.sql';",
        "SELECT 'migrations/b.sql';",
    ]


def test_new_database_stops_at_the_first_failure(tmp_path, monkeypatch, capsys, db_env) -> None:
    _order_file(tmp_path, monkeypatch, ["schema/a.sql", "schema/b.sql", "schema/c.sql"])
    applied = _record_applies(monkeypatch, fail_at=2)
    monkeypatch.setattr(sys, "argv", ["apply_sql.py", "--new-database", "--apply"])

    assert apply_sql.main() == 3
    assert applied == ["SELECT 'schema/a.sql';", "SELECT 'schema/b.sql';"]
    assert "2번째 파일에서 멈췄다" in unicodedata.normalize("NFC", capsys.readouterr().out)


def test_new_database_refuses_a_database_that_already_has_objects(
    tmp_path, monkeypatch, db_env
) -> None:
    _order_file(tmp_path, monkeypatch, ["schema/a.sql"])
    applied = _record_applies(monkeypatch)
    monkeypatch.setattr(apply_sql, "_haetdeul_object_count", lambda: 7)
    monkeypatch.setattr(sys, "argv", ["apply_sql.py", "--new-database", "--apply"])

    with pytest.raises(SystemExit) as stopped:
        apply_sql.main()

    assert "빈 DB 가 아니다" in unicodedata.normalize("NFC", str(stopped.value))
    assert applied == []


def test_new_database_without_apply_only_lists(
    tmp_path, monkeypatch, capsys, 붙으면_터지는_psycopg
) -> None:
    _order_file(tmp_path, monkeypatch, ["schema/b.sql", "schema/a.sql"])
    monkeypatch.setattr(sys, "argv", ["apply_sql.py", "--new-database"])

    assert apply_sql.main() == 0

    out = unicodedata.normalize("NFC", capsys.readouterr().out)
    assert out.index("schema/b.sql") < out.index("schema/a.sql")
    assert "안 돌렸다" in out


def test_new_database_rejects_a_missing_or_repeated_entry(
    tmp_path, monkeypatch, 붙으면_터지는_psycopg
) -> None:
    for listed, message in (
        (["database/schema/a.sql", "database/schema/none.sql"], "파일이 없다"),
        (["database/schema/a.sql", "database/schema/a.sql"], "두 번"),
    ):
        _order_file(tmp_path, monkeypatch, ["schema/a.sql"], listed=listed)
        monkeypatch.setattr(sys, "argv", ["apply_sql.py", "--new-database"])
        with pytest.raises(SystemExit) as stopped:
            apply_sql.main()
        assert message in unicodedata.normalize("NFC", str(stopped.value))


def test_new_database_does_not_take_a_file_argument(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["apply_sql.py", "x.sql", "--new-database"])

    with pytest.raises(SystemExit) as stopped:
        apply_sql.main()

    assert stopped.value.code == 2
