"""물류 계층의 방향 — 2026-09-30 재구성 BL-015.

```text
마스터 bootstrap → app/logistics/adapter.py(봉투 분기 · 등록소 표면) → service
                   service → domain · repository · readmodel
마스터 흐름(출고 · 하루 단계 · 점검) → service (마스터가 쥔 연결을 넘긴다)
마스터 보고서 → readmodel (자기 연결을 넘긴다)
화면 app/api/logistics/presenter.py → readmodel 입구(한 판 = 조회 연결 하나) → repository · domain
```

이 파일이 잠그는 것:

1. 계층별로 들일 수 있는 것 — schemas 는 다른 계층을 모르고, domain 은 DB · HTTP · LLM 호출 ·
   파일 · 환경 · 시계를 모르고(시간대 상수 `SEOUL` 하나만), repository 는 판단 · 조립 · 순서를
   모르고, 어댑터는 service · domain · schemas 만 부른다. FastAPI 를 모른다.
2. 연결 — repository 는 빌리지도 commit 하지도 않는다. readmodel 은 조회 연결만 빌린다.
   service 에서 연결을 빌리는 곳은 둘뿐이다(`run_history` 저장 한 트랜잭션 · `status_question`
   조회 연결). 그 밖의 service — 마스터가 연결을 넘기는 경로 전부 — 는 빌리지도 commit ·
   rollback 하지도 않는다.
3. SQL 은 repository 에만 있고, 쓰기 전역 잠금(`pg_advisory`)은 `repository/locks.py` 한 곳에
   좌표 넷과 획득 순서 표로 있다.
4. 밖에서 들이는 자리 — 어댑터는 마스터 bootstrap 만, 화면은 readmodel · domain · schemas 만,
   마스터는 repository 를 들이지 않는다.
5. 물류 안에서 모듈 맨 위 import 순환이 없고, 다른 모듈의 private 이름을 들이지 않는다
   (예외 하나 — `_record_disposal_move`, 폐기 저수준 원장 함수를 공개 API 로 열지 않으려는 뜻).

★ 스캐너는 `import_scan.py` 한 벌이다(함수 안 · 문자열 동적 import 까지 센다). 검사마다
  **심어 둔 모양을 스스로 잡는지**도 잰다 — 0건을 세는 검사가 공짜로 초록이 되지 않게.
"""

from __future__ import annotations

import ast
from pathlib import Path

from import_scan import is_under, module_refs, python_files

import app

_APP = Path(app.__file__).parent
_LOGISTICS = _APP / "logistics"
_BACKEND = _APP.parent
_PKG = "app.logistics"

#: 계층 → 들여도 되는 물류 계층(자기 계층 포함). `llm.schemas` 는 LLM 입출력 타입이다.
_MAY_IMPORT: dict[str, frozenset[str]] = {
    "schemas": frozenset({"schemas", "llm.schemas"}),
    "domain": frozenset({"domain", "schemas", "llm.schemas"}),
    "repository": frozenset({"repository", "schemas"}),
    "readmodel": frozenset({"readmodel", "repository", "domain", "schemas"}),
    "service": frozenset(
        {"service", "readmodel", "repository", "domain", "schemas", "llm", "llm.schemas"}
    ),
    "llm": frozenset({"llm", "llm.schemas", "domain", "schemas"}),
    "adapter": frozenset({"service", "domain", "schemas"}),
    "__init__": frozenset(),
}

#: 계층 → 들이면 안 되는 바깥 모듈(앞머리 일치).
_MAY_NOT_TAKE: dict[str, tuple[str, ...]] = {
    "schemas": ("psycopg", "fastapi", "app.core.db", "app.core.settings", "app.api", "app.master"),
    "domain": (
        "psycopg", "fastapi", "httpx", "requests", "urllib", "os", "pathlib", "dotenv",
        "app.core.db", "app.core.settings", "app.api", "app.master",
    ),
    "repository": ("fastapi", "app.core.db", "app.api", "app.master"),
    "readmodel": ("fastapi", "app.api", "app.master"),
    "service": ("fastapi", "app.api", "app.master"),
    "llm": ("fastapi", "psycopg", "app.core.db", "app.api", "app.master"),
    "adapter": ("fastapi", "psycopg", "app.core.db", "app.api", "app.master"),
    "__init__": (),
}

#: service 에서 연결을 빌려도 되는 파일과 그 방법 — 마스터가 연결을 넘기지 않는 입구뿐이다.
_SERVICE_MAY_BORROW = {
    "service/run_history.py": frozenset({"connection", "transaction"}),
    "service/status_question.py": frozenset({"read_connection"}),
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _rel(path: Path) -> str:
    return path.relative_to(_LOGISTICS).as_posix()


def _layer(path: Path) -> str:
    parts = path.relative_to(_LOGISTICS).parts
    return parts[0] if len(parts) > 1 else path.stem


def _layer_of_module(name: str) -> str | None:
    """`app.logistics.<계층>…` → 계층. 물류 밖이거나 패키지 뿌리면 `None`."""
    parts = name.split(".")
    if parts[:2] != ["app", "logistics"] or len(parts) < 3:
        return None
    if parts[2] == "llm" and len(parts) > 3 and parts[3] == "schemas":
        return "llm.schemas"
    return parts[2]


def _code_only(source: str) -> str:
    """docstring 과 `#` 주석을 걷어낸 **실행되는 코드**."""
    tree = ast.parse(source)
    dead: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            dead.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return "\n".join(
        line.split("#", 1)[0]
        for number, line in enumerate(source.splitlines(), start=1)
        if number not in dead
    )


def _logistics_files() -> list[Path]:
    return python_files(_LOGISTICS)


# ---------------------------------------------------------------------------
# 1. 계층별로 들일 수 있는 것
# ---------------------------------------------------------------------------


def _layer_violations(layer: str, source: str) -> list[str]:
    bad: list[str] = []
    for line, shape, names in module_refs(source):
        for name in names:
            target = _layer_of_module(name)
            if target is not None and target not in _MAY_IMPORT[layer]:
                bad.append(f"{line}: {shape} → {target}")
                break
            if any(is_under(name, banned) for banned in _MAY_NOT_TAKE[layer]):
                bad.append(f"{line}: {shape}")
                break
    return bad


def test_layers_import_only_what_they_may() -> None:
    found = {
        _rel(path): hits
        for path in _logistics_files()
        if (hits := _layer_violations(_layer(path), _read(path)))
    }
    assert found == {}, found


def test_layer_check_catches_planted_imports() -> None:
    assert _layer_violations("repository", "from app.logistics.domain.tools import x\n")
    assert _layer_violations("repository", "from app.core import db as core_db\n")
    assert _layer_violations("schemas", "from app.logistics.service import outbound\n")
    assert _layer_violations("domain", "import os\n")
    assert _layer_violations("domain", "from app.logistics.readmodel.console import y\n")
    assert _layer_violations("readmodel", "from app.logistics.service.outbound import z\n")
    assert _layer_violations("adapter", "from app.logistics.repository.ledger import w\n")
    assert _layer_violations("service", "from fastapi import Depends\n")
    assert _layer_violations("domain", "from app.logistics import adapter\n")
    assert not _layer_violations("readmodel", "from app.logistics.repository.rows import cell\n")
    assert not _layer_violations("schemas", "from app.logistics.llm.schemas import Interp\n")


def _clock_reads(source: str) -> list[str]:
    """시계 함수 부름 · 시계 모듈의 상수 밖 이름 들이기."""
    bad: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"now", "today", "utcnow"} and isinstance(
                node.func.value, ast.Name
            ) and node.func.value.id in {"datetime", "date"}:
                bad.append(f"{node.lineno}: {ast.unparse(node.func)}()")
        elif isinstance(node, ast.ImportFrom) and node.module == "app.core.clock":
            names = {alias.name for alias in node.names}
            if names - {"SEOUL"}:
                bad.append(f"{node.lineno}: from app.core.clock import {sorted(names)}")
        elif isinstance(node, ast.Import) and any(a.name == "app.core.clock" for a in node.names):
            bad.append(f"{node.lineno}: import app.core.clock")
    return bad


def test_domain_reads_no_clock_only_the_timezone_constant() -> None:
    """★ 날짜는 부르는 쪽이 넘긴다. domain 이 읽는 것은 시간대 상수 `SEOUL` 하나뿐이다."""
    found = {
        _rel(path): hits
        for path in python_files(_LOGISTICS / "domain")
        if (hits := _clock_reads(_read(path)))
    }
    assert found == {}, found


def test_clock_check_catches_planted_reads() -> None:
    assert _clock_reads("x = datetime.now()\n")
    assert _clock_reads("x = date.today()\n")
    assert _clock_reads("from app.core.clock import seoul_now\n")
    assert _clock_reads("from app.core.clock import SEOUL, today_in_seoul\n")
    assert not _clock_reads("from app.core.clock import SEOUL\n")


# ---------------------------------------------------------------------------
# 2. 연결 — 누가 빌리고 누가 commit 하나
# ---------------------------------------------------------------------------


def _connection_calls(source: str) -> set[str]:
    """`core_db.X(...)` 부름 · `.commit()` · `.rollback()` · `.transaction(...)` 부름."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if isinstance(owner, ast.Name) and owner.id == "core_db":
                found.add(f"core_db.{node.func.attr}")
            elif node.func.attr in {"commit", "rollback", "transaction"}:
                found.add(f".{node.func.attr}")
    return found


def test_repository_neither_borrows_nor_ends_transactions() -> None:
    found = {
        _rel(path): calls
        for path in python_files(_LOGISTICS / "repository")
        if (calls := _connection_calls(_read(path)))
    }
    assert found == {}, found


#: readmodel 에서 조회 연결(`read_connection`) 말고 `connection()` + `transaction` 을 쓰는 함수 —
#: 종전 물류 조회가 그 경계로 읽던 자리뿐이다(2026-09-30 재구성 BL-015 · 종전 경계 복원).
#: 운송 계약의 `.transaction` 은 받은 연결을 오염시키지 않으려는 SAVEPOINT 다.
_READMODEL_TRANSACTIONS: dict[tuple[str, str], frozenset[str]] = {
    ("readmodel/console.py", "read_console_page"): frozenset(
        {"core_db.connection", "core_db.transaction"}
    ),
    ("readmodel/console.py", "read_stock_chart"): frozenset(
        {"core_db.connection", "core_db.transaction"}
    ),
    ("readmodel/current.py", "_schedule_lists"): frozenset(
        {"core_db.connection", "core_db.transaction"}
    ),
    ("readmodel/current.py", "_delivery_route"): frozenset(
        {"core_db.connection", "core_db.transaction", ".transaction"}
    ),
}


def _readmodel_connection_calls(rel: str, source: str) -> dict[str, set[str]]:
    """맨 위 함수마다 조회 연결 밖의 대여 · 트랜잭션 · commit · rollback 부름."""
    found: dict[str, set[str]] = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef):
            calls = _connection_calls(ast.get_source_segment(source, node) or "")
            calls -= {"core_db.read_connection"}
            calls -= _READMODEL_TRANSACTIONS.get((rel, node.name), frozenset())
            if calls:
                found[node.name] = calls
    return found


def test_readmodel_borrows_read_connections_and_keeps_the_old_transaction_blocks() -> None:
    """★ 조회는 `read_connection` 을 빌린다. `connection()` + `transaction` 은 종전 경계를 옮긴
    네 함수(화면 한 판 · 재고 그래프 · 일정 목록 · 운송 계약)에만 있고, commit · rollback 을
    직접 부르는 곳은 없다(끝맺음은 `core_db.transaction` 이 한다)."""
    found = {
        _rel(path): calls
        for path in python_files(_LOGISTICS / "readmodel")
        if (calls := _readmodel_connection_calls(_rel(path), _read(path)))
    }
    assert found == {}, found


def test_readmodel_connection_check_catches_planted_calls() -> None:
    planted = (
        "def other():\n"
        "    with core_db.connection() as c, core_db.transaction(c):\n"
        "        pass\n"
    )
    assert _readmodel_connection_calls("readmodel/console.py", planted) == {
        "other": {"core_db.connection", "core_db.transaction"}
    }
    commit = "def read_console_page():\n    conn.commit()\n"
    assert _readmodel_connection_calls("readmodel/console.py", commit) == {
        "read_console_page": {".commit"}
    }


def test_only_two_services_borrow_and_the_rest_never_commit() -> None:
    """🔴 마스터가 연결을 넘기는 경로(등록소 · 출고 흐름 · 하루 단계 · 점검)의 service 는
    받은 연결만 쓴다 — 빌리지도, commit · rollback 하지도, 트랜잭션을 열지도 않는다."""
    found: dict[str, set[str]] = {}
    for path in python_files(_LOGISTICS / "service"):
        calls = _connection_calls(_read(path))
        allowed = {f"core_db.{name}" for name in _SERVICE_MAY_BORROW.get(_rel(path), ())}
        if _rel(path) not in _SERVICE_MAY_BORROW and _takes_connection_module(_read(path)):
            calls.add("import app.core.db")
        if calls - allowed:
            found[_rel(path)] = calls - allowed
    assert found == {}, found


def _takes_connection_module(source: str) -> bool:
    """연결 모듈(`app.core.db`)을 들이나 — 부르지 않아도 들이면 부를 수 있다."""
    return any(
        is_under(name, "app.core.db")
        for _line, _shape, names in module_refs(source)
        for name in names
    )


def test_adapter_neither_borrows_nor_commits() -> None:
    assert _connection_calls(_read(_LOGISTICS / "adapter.py")) == set()


def test_connection_check_catches_planted_calls() -> None:
    assert _connection_calls("with core_db.connection() as c:\n    pass\n") == {
        "core_db.connection"
    }
    assert _connection_calls("conn.commit()\n") == {".commit"}
    assert _connection_calls("with core_db.transaction(conn):\n    pass\n") == {
        "core_db.transaction"
    }
    assert _takes_connection_module("from app.core import db as core_db\n")
    assert _takes_connection_module("import app.core.db\n")
    assert not _takes_connection_module("from app.core.text import to_decimal\n")
    assert _connection_calls("conn.rollback()\n") == {".rollback"}


# ---------------------------------------------------------------------------
# 3. SQL 과 잠금의 자리
# ---------------------------------------------------------------------------


def _sql_marks(source: str) -> list[str]:
    """SQL 을 짓거나 보내는 자리 — `psycopg.sql` 들이기 · `.cursor()` · `.execute()`."""
    bad: list[str] = []
    for line, shape, names in module_refs(source):
        if any(name in {"psycopg.sql", "psycopg.sql.SQL"} for name in names):
            bad.append(f"{line}: {shape}")
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"cursor", "execute", "executemany"}
        ):
            bad.append(f"{node.lineno}: .{node.func.attr}()")
    return bad


def test_sql_lives_only_in_repository() -> None:
    found = {
        _rel(path): hits
        for path in _logistics_files()
        if _layer(path) != "repository" and (hits := _sql_marks(_read(path)))
    }
    assert found == {}, found


def test_sql_check_catches_planted_sql() -> None:
    assert _sql_marks("from psycopg import sql\n")
    assert _sql_marks("with conn.cursor() as c:\n    c.execute('SELECT 1')\n")
    assert not _sql_marks("import psycopg\nerr = psycopg.Error\n")


def test_advisory_locks_live_in_one_place_with_their_order() -> None:
    """🔴 잠금 좌표와 획득 순서는 한 표에서 읽는다 — 흩어지면 새 경로가 순서를 모른 채 잡는다."""
    holders = [
        _rel(path)
        for path in _logistics_files()
        if "pg_advisory" in _code_only(_read(path))
    ]
    assert holders == ["repository/locks.py"], holders

    from app.logistics.repository import locks

    assert {
        name: (getattr(locks, f"{name}_LOCK_CLASSID"), getattr(locks, f"{name}_LOCK_OBJID"))
        for name in ("LEDGER", "ARRIVAL", "OUTBOUND", "WAREHOUSE")
    } == {
        "LEDGER": (20260905, 1),
        "ARRIVAL": (20260905, 2),
        "OUTBOUND": (20260905, 3),
        "WAREHOUSE": (20260905, 4),
    }
    lock_functions = {
        node.name
        for node in ast.parse(_read(_LOGISTICS / "repository" / "locks.py")).body
        if isinstance(node, ast.FunctionDef)
    }
    assert lock_functions == {
        "lock_ledger_writes",
        "lock_arrival_writes",
        "lock_outbound_writes",
        "lock_warehouse_writes",
    }
    doc = locks.__doc__ or ""
    for flow in ("입고(도착 처리)", "출고", "자동 유지보수", "배치"):
        assert flow in doc, f"잠금 순서 표에 {flow} 줄이 없다"


#: 한 함수 안에서 직접 잡는 잠금의 순서 — 표(`repository/locks.py`)의 전순서다.
#: 도착(②) · 출고(③) 는 원장(①)보다 먼저, 배치(④)는 원장 뒤, 도착과 출고는 한 흐름에 없다.
_LOCK_RANK = {
    "lock_arrival_writes": 0,
    "lock_outbound_writes": 0,
    "lock_ledger_writes": 1,
    "lock_warehouse_writes": 2,
}


def _lock_order_violations(source: str) -> list[str]:
    bad: list[str] = []
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, ast.FunctionDef):
            continue
        calls = sorted(
            (node.lineno, node.col_offset, node.func.id)
            for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _LOCK_RANK
        )
        names = [name for *_, name in calls]
        ranks = [_LOCK_RANK[name] for name in names]
        if ranks != sorted(ranks) or {"lock_arrival_writes", "lock_outbound_writes"} <= set(names):
            bad.append(f"{fn.name}: {names}")
    return bad


def test_locks_taken_in_one_function_follow_the_table() -> None:
    found = {
        _rel(path): hits
        for path in python_files(_LOGISTICS / "service")
        if (hits := _lock_order_violations(_read(path)))
    }
    assert found == {}, found


def test_lock_order_check_catches_planted_inversions() -> None:
    assert _lock_order_violations(
        "def f(conn):\n    lock_ledger_writes(conn)\n    lock_outbound_writes(conn)\n"
    )
    assert _lock_order_violations(
        "def f(conn):\n    lock_arrival_writes(conn)\n    lock_outbound_writes(conn)\n"
    )
    assert not _lock_order_violations(
        "def f(conn):\n    lock_outbound_writes(conn)\n    lock_ledger_writes(conn)\n"
        "    lock_warehouse_writes(conn)\n"
    )


# ---------------------------------------------------------------------------
# 4. 밖에서 들이는 자리
# ---------------------------------------------------------------------------


def _importers(root: Path, predicate) -> list[str]:
    return sorted(
        path.relative_to(_APP).as_posix()
        for path in python_files(root)
        if not is_under(path.relative_to(_APP).as_posix().replace("/", "."), "logistics")
        and any(
            predicate(name) for _line, _shape, names in module_refs(_read(path)) for name in names
        )
    )


def test_only_the_master_bootstrap_imports_the_adapter() -> None:
    """★ 어댑터(봉투 분기 · 등록소 표면)는 마스터가 등록할 때 한 번 들인다 — 다른 자리가
    들이면 봉투를 거치지 않는 두 번째 입구가 생긴다."""
    # ★ 2026-09-30 재구성 BL-018: 등록소 조립은 `master/registry/bootstrap.py` 다.
    assert _importers(_APP, lambda name: is_under(name, f"{_PKG}.adapter")) == [
        "master/registry/bootstrap.py"
    ]


def test_screen_reads_logistics_only_through_readmodel_domain_and_schemas() -> None:
    """🔴 화면은 물류 DB 를 직접 다루지 않는다 — repository · service · 어댑터를 들이지 않는다."""
    allowed = tuple(f"{_PKG}.{layer}" for layer in ("readmodel", "domain", "schemas"))
    reached = _importers(
        _APP / "api",
        lambda name: is_under(name, _PKG)
        and name != _PKG
        and not any(is_under(name, ok) for ok in allowed),
    )
    assert reached == []


def test_master_does_not_reach_logistics_sql() -> None:
    assert _importers(_APP / "master", lambda name: is_under(name, f"{_PKG}.repository")) == []


def test_logistics_knows_no_fastapi() -> None:
    reached = [
        _rel(path)
        for path in _logistics_files()
        if any(
            is_under(name, "fastapi")
            for _line, _shape, names in module_refs(_read(path))
            for name in names
        )
    ]
    assert reached == []


# ---------------------------------------------------------------------------
# 5. 순환 · private 이름
# ---------------------------------------------------------------------------


def _module_of(path: Path) -> str:
    rel = path.relative_to(_BACKEND).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _top_level_imports(source: str) -> set[str]:
    """모듈 맨 위(함수 · `TYPE_CHECKING` 밖) import 가 가리키는 모듈 이름들."""
    found: set[str] = set()
    for node in ast.parse(source).body:
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
            found |= {f"{node.module}.{alias.name}" for alias in node.names}
        elif isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
    return found


def _cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    seen: set[str] = set()
    stack: list[str] = []
    on_stack: set[str] = set()
    out: list[list[str]] = []

    def visit(node: str) -> None:
        seen.add(node)
        stack.append(node)
        on_stack.add(node)
        for nxt in sorted(graph.get(node, ())):
            if nxt in on_stack:
                out.append([*stack[stack.index(nxt):], nxt])
            elif nxt not in seen:
                visit(nxt)
        stack.pop()
        on_stack.discard(node)

    for node in sorted(graph):
        if node not in seen:
            visit(node)
    return out


def test_no_top_level_import_cycles_inside_logistics() -> None:
    modules = {_module_of(path): path for path in _logistics_files()}
    graph = {
        name: {
            target
            for target in _top_level_imports(_read(path))
            if target in modules and target != name
        }
        for name, path in modules.items()
    }
    assert _cycles(graph) == []


def test_cycle_check_catches_a_planted_cycle() -> None:
    assert _cycles({"a": {"b"}, "b": {"c"}, "c": {"a"}}) == [["a", "b", "c", "a"]]
    assert _cycles({"a": {"b"}, "b": set()}) == []


#: 의도된 private 이름 공유 — (들이는 파일, 모듈, 이름). 늘면 빨간불이다.
_PRIVATE_SHARED = {
    ("service/disposal.py", f"{_PKG}.service.ledger", "_record_disposal_move"),
}


def _private_imports(path: Path) -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for node in ast.walk(ast.parse(_read(path))):
        if isinstance(node, ast.ImportFrom) and node.module and is_under(node.module, _PKG):
            for alias in node.names:
                if alias.name.startswith("_") and not alias.name.startswith("__"):
                    found.add((_rel(path), node.module, alias.name))
    return found


def test_no_private_names_cross_modules_but_the_one_intended() -> None:
    shared = set().union(*(_private_imports(path) for path in _logistics_files()))
    assert shared == _PRIVATE_SHARED
