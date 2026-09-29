"""재무 계층의 방향 — 2026-09-29 재구성 BL-014.

```text
화면   → app/finance/router.py(HTTP 만) → service · readmodel → domain · repository
마스터 → (등록소) → app/finance/adapter.py(번역만) → service · readmodel → domain · repository
마스터 ask → 재무 service (라우터와 같은 함수)
```

이 파일이 잠그는 것:

1. 쓰기 — 재무 라우터와 마스터 ask 가 **같은 service 함수**를 부르고, 라우터 함수를 다른
   파이썬 코드가 import 하지 않는다. service 는 HTTP 상태를 모른다.
2. 연결 — HTTP · ask 쓰기 service 는 받은 연결에 트랜잭션을 **스스로** 연다. 마스터가 연결을
   넘기고 commit 하는 경로의 service 는 연결을 빌리지도 commit 하지도 않는다. readmodel 은
   조회 연결만 빌린다.
3. 계층별로 들일 수 있는 것 — domain 은 DB · HTTP · LLM · 파일 · 환경을 모르고, schemas 는 다른
   계층을 모르고, SQL 은 repository 에만 있고, 어댑터는 번역만 한다.
4. 재무 안에서 모듈 맨 위 import 순환이 없고, 다른 모듈의 private 이름을 들이지 않는다.

★ 스캐너는 `import_scan.py` 한 벌이다(함수 안 · 문자열 동적 import 까지 센다). 검사마다
  **심어 둔 모양을 스스로 잡는지**도 잰다 — 0건을 세는 검사가 공짜로 초록이 되지 않게.

⚠️ 남은 빚(BL-020 · LLM 계층화 범위)은 **지금 모양 그대로 못박는다** — 늘면 빨간불이다.
  Controller ↔ Harness ↔ Planner 사이 함수 안 import 셋, `llm/` 안 private 이름 공유.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest
from import_scan import is_under, module_refs, python_files

import app

_APP = Path(app.__file__).parent
_FINANCE = _APP / "finance"
_BACKEND = _APP.parent
_ROUTER = _FINANCE / "router.py"
_ADAPTER = _FINANCE / "adapter.py"
_ASK = _APP / "master" / "ask_service.py"


def _module_name(path: Path) -> str:
    rel = path.relative_to(_BACKEND).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imported(source: str) -> set[str]:
    """그 원문이 들이는 모듈 이름 전부 (함수 안 · 동적 import 포함)."""
    return {name for _line, _shape, names in module_refs(source) for name in names}


def _finance_files(layer: str | None = None) -> list[Path]:
    root = _FINANCE if layer is None else _FINANCE / layer
    return python_files(root)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _rel(path: Path) -> str:
    return path.relative_to(_BACKEND).as_posix()


# ---------------------------------------------------------------------------
# 1. 쓰기 — 라우터와 마스터 ask 가 같은 service 를 부른다
# ---------------------------------------------------------------------------


def _bindings(tree: ast.Module) -> dict[str, str]:
    """모듈 맨 위 이름 → 그 이름이 가리키는 **점 경로** (`from X import Y as Z` → `Z: X.Y`)."""
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                found[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found[alias.asname or alias.name.split(".")[0]] = alias.name
    return found


def _resolved_calls(path: Path, function: str) -> set[str]:
    """그 함수 본문이 부르는 것을 **어디의 무엇인지**(점 경로)로 푼다.

    `f(...)` 는 `from X import f` 로, `m.f(...)` 는 `from P import m` · `import P.m as m` 로 푼다.
    """
    tree = ast.parse(_read(path))
    bindings = _bindings(tree)
    target = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == function
        ),
        None,
    )
    assert target is not None, f"{function} 이 없다"
    calls: set[str] = set()
    for call in ast.walk(target):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if isinstance(func, ast.Name) and func.id in bindings:
            calls.add(bindings[func.id])
        elif (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in bindings
        ):
            calls.add(f"{bindings[func.value.id]}.{func.attr}")
    return calls


_WRITES = {
    "credit-limit": (
        "register_credit_limit",
        "app.finance.service.credit_limits.change_credit_limit",
    ),
    "expense-create": (
        "create_operating_expense",
        "app.finance.service.expenses.accrue_operating_expense",
    ),
    "expense-settle": (
        "settle_operating_expense",
        "app.finance.service.expenses.pay_accrued_expense",
    ),
    "expense-cancel": (
        "cancel_operating_expense",
        "app.finance.service.expenses.cancel_accrued_expense",
    ),
    "collection": (
        "record_receivable_collection",
        "app.finance.service.collections.record_collection",
    ),
    "cash-adjustment": (
        "create_cash_adjustment",
        "app.finance.service.cash_adjustments.apply_cash_adjustment",
    ),
}


@pytest.mark.parametrize("write", sorted(_WRITES))
def test_router_and_ask_call_the_same_finance_service(write):
    """🔴 재무 라우터 핸들러와 마스터 ask 가 **같은 함수 객체**를 부른다.

    한쪽만 고쳐지는 날이 없다.
    """
    handler, service = _WRITES[write]

    assert service in _resolved_calls(_ROUTER, handler), _resolved_calls(_ROUTER, handler)
    assert service in _resolved_calls(_ASK, "_domain_write"), write


def test_the_call_resolver_sees_both_call_shapes():
    tree_source = (
        "from app.finance.service import expenses as expense_service\n"
        "from app.finance.service.credit_limits import change_credit_limit\n"
        "def h(conn, change):\n"
        "    expense_service.accrue_operating_expense(conn, change)\n"
        "    change_credit_limit(conn, change)\n"
    )
    planted = _BACKEND / "tests" / "architecture" / "__resolver_probe.py"
    planted.write_text(tree_source, encoding="utf-8")
    try:
        assert _resolved_calls(planted, "h") == {
            "app.finance.service.expenses.accrue_operating_expense",
            "app.finance.service.credit_limits.change_credit_limit",
        }
    finally:
        planted.unlink()


def test_no_module_imports_the_finance_router_except_the_app_root():
    """🔴 라우터 핸들러는 HTTP 입구다. 다른 코드가 함수로 부르면 `HTTPException` 이 샌다.

    ★ 2026-09-29 재구성 BL-014 전에는 마스터 ask 가 재무 라우터 핸들러 여섯을 불렀다.
    """
    importers = sorted(
        _module_name(path)
        for path in python_files(_APP)
        if any(is_under(name, "app.finance.router") for name in _imported(_read(path)))
    )

    assert importers == ["app.main"]


def test_the_router_scan_catches_a_planted_handler_import():
    planted = "def f():\n    from app.finance.router import register_credit_limit\n"
    assert any(is_under(name, "app.finance.router") for name in _imported(planted))


def test_only_the_registry_and_the_agent_route_import_the_finance_adapter():
    """어댑터는 마스터 봉투 번역이다. 등록소(`bootstrap`)가 파트를 등록하고, `POST /finance/agent`
    가 같은 봉투 입구(`finance_port`)를 HTTP 로 연다(종전과 같다). 그 밖에는 들이지 않는다.
    """
    importers = sorted(
        _module_name(path)
        for path in python_files(_APP)
        if any(is_under(name, "app.finance.adapter") for name in _imported(_read(path)))
    )

    assert importers == ["app.finance.router", "app.master.bootstrap"]
    router_takes = {
        name for name in _imported(_read(_ROUTER)) if is_under(name, "app.finance.adapter")
    }
    assert router_takes == {"app.finance.adapter", "app.finance.adapter.finance_port"}


#: 어댑터가 들일 수 있는 것 — 봉투 계약 · 재무 모델 · 재무 service · 매입 제안 계약(검증 모양) ·
#: 연결 **타입**(마스터가 넘기는 연결을 받는 Protocol 표면).
#: **SQL · repository · readmodel 은 없다.**
_ADAPTER_ALLOWED = (
    "app.contracts.envelope",
    "app.contracts.parts",
    "app.finance.schemas",
    "app.finance.service",
    "app.purchase_agent.schemas.proposal",
)


def test_the_adapter_only_translates():
    source = _read(_ADAPTER)
    imported = {name for name in _imported(source) if name.startswith("app.")}
    offenders = sorted(
        name for name in imported if not any(is_under(name, ok) for ok in _ADAPTER_ALLOWED)
    )

    assert offenders == [], offenders
    assert {n for n in _imported(source) if n.split(".")[0] == "psycopg"} == {
        "psycopg",
        "psycopg.Connection",
    }
    assert not _code_uses(source) & _SQL_USES


def test_the_service_decides_no_http_status():
    """🔴 상태 코드를 정하는 것은 라우터다. service 는 업무 결과 · 업무 거절만 낸다."""
    offenders = {
        _rel(path)
        for path in _finance_files("service")
        if _code_uses(_read(path)) & {"HTTPException", "status_code"}
        or any(n.split(".")[0] in {"fastapi", "starlette"} for n in _imported(_read(path)))
    }

    assert offenders == set()
    assert _code_uses("raise HTTPException(status_code=409)") >= {"HTTPException", "status_code"}


# ---------------------------------------------------------------------------
# 2. 연결 · 트랜잭션 — 누가 빌리고 누가 commit 하는가
# ---------------------------------------------------------------------------


def _function_source(path: Path, function: str) -> str:
    source = _read(path)
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == function:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{_rel(path)} 에 {function} 이 없다")


#: HTTP · ask 가 부르는 쓰기 — 받은 연결에 **한 요청 = 한 트랜잭션**을 스스로 연다.
_OWN_TRANSACTION = [
    ("credit_limits", "change_credit_limit"),
    ("expenses", "accrue_operating_expense"),
    ("expenses", "pay_accrued_expense"),
    ("expenses", "cancel_accrued_expense"),
    ("collections", "record_collection"),
    ("cash_adjustments", "apply_cash_adjustment"),
]

#: 마스터가 연결을 넘기고 commit 하는 경로 — 재무는 **빌리지도 commit 하지도** 않는다.
_MASTER_CONNECTION = [
    ("day_open", "is_day_open"),
    ("day_open", "open_finance_day"),
    ("transition", "persist_finance_transition"),
    #  `close_finance_day(conn=…)` 는 받은 연결을 이 함수에 넘긴다 — 연결 없이 부르는 대체
    #  경로(앱 안 호출 0)만 스스로 빌린다. 등록소 표면은 연결을 넘긴다(아래 검사).
    ("closing", "close_finance_day_on"),
    ("cancellation", "cancel_finance_payables"),
    ("collections", "apply_collection_event"),
    ("collections", "apply_explicit_collection"),
    ("receivables", "confirm_receivable"),
    ("expenses", "settle_due_expenses"),
    ("expenses", "settle_expense"),
    ("cash_adjustments", "record_cash_adjustment"),
]

def _borrow_or_end(source: str) -> list[str]:
    """원문이 **코드로** 연결을 빌리거나(`core_db.connection` · `read_connection` · `transaction`)
    끝내는(`commit` · `rollback` · `close`) 자리. 문자열 · 주석 · docstring 은 세지 않는다.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        owner = node.func.value
        if (
            isinstance(owner, ast.Name)
            and owner.id == "core_db"
            and node.func.attr in {"connection", "read_connection", "transaction"}
        ):
            found.append(f"core_db.{node.func.attr}")
        elif node.func.attr in {"commit", "rollback", "close"}:
            found.append(node.func.attr)
    return sorted(found)


@pytest.mark.parametrize(("module", "function"), _OWN_TRANSACTION)
def test_http_and_ask_writes_open_their_transaction_on_the_given_connection(module, function):
    body = _function_source(_FINANCE / "service" / f"{module}.py", function)

    assert "core_db.transaction(conn)" in body
    #  연결은 부르는 쪽(HTTP `Depends` · ask 의 `core_db.connection()`)이 빌린다.
    assert _borrow_or_end(body) == ["core_db.transaction"]


@pytest.mark.parametrize(("module", "function"), _MASTER_CONNECTION)
def test_master_connection_paths_never_borrow_or_commit(module, function):
    body = _function_source(_FINANCE / "service" / f"{module}.py", function)

    assert _borrow_or_end(body) == [], function


def test_the_closing_surface_passes_the_master_connection():
    """등록소 마감 표면이 마스터 연결을 넘긴다 — 안 넘기면 재무가 자기 연결로 따로 commit 한다."""
    body = _function_source(_ADAPTER, "close")

    assert "close_finance_day(as_of=as_of, sim_run_id=sim_run_id, conn=conn)" in body


def test_the_transaction_scan_catches_planted_shapes():
    planted = (
        "def f(conn):\n"
        "    with core_db.connection() as own, core_db.transaction(own):\n"
        "        conn.commit()\n"
        "    '''core_db.read_connection() 은 말만 한다'''\n"
    )
    assert _borrow_or_end(planted) == ["commit", "core_db.connection", "core_db.transaction"]


def test_readmodels_only_borrow_read_connections():
    """조회는 쓰기 연결 · 트랜잭션을 열지 않는다 — 열면 화면이 장부를 바꿀 길이 생긴다."""
    offenders = {
        _rel(path): uses
        for path in _finance_files("readmodel")
        if (uses := [u for u in _borrow_or_end(_read(path)) if u != "core_db.read_connection"])
    }

    assert offenders == {}
    assert any(
        "core_db.read_connection" in _borrow_or_end(_read(path))
        for path in _finance_files("readmodel")
    )


def test_repositories_run_on_the_given_connection_only():
    """repository 는 받은 연결로 실행만 한다 — 빌리지도 끝내지도 않는다."""
    offenders = {_rel(path): _borrow_or_end(_read(path)) for path in _finance_files("repository")}

    assert {path: words for path, words in offenders.items() if words} == {}


# ---------------------------------------------------------------------------
# 3. 계층별로 들일 수 있는 것
# ---------------------------------------------------------------------------

_OTHER_FINANCE = (
    "app.finance.service",
    "app.finance.repository",
    "app.finance.readmodel",
    "app.finance.llm",
    "app.finance.adapter",
    "app.finance.router",
)

#: 계층마다 **들이면 안 되는** 모듈. 표준 라이브러리 · pydantic 은 막지 않는다.
_FORBIDDEN: dict[str, tuple[str, ...]] = {
    #  판단 · 계산: 입력 → 출력만. DB · HTTP · LLM · 파일 · 환경 · 시계를 모른다.
    "domain": (
        "psycopg",
        "psycopg_pool",
        "fastapi",
        "starlette",
        "langgraph",
        "langchain_core",
        "urllib",
        "dotenv",
        "os",
        "pathlib",
        "app.core.db",
        "app.core.settings",
        "app.core.clock",
        "app.api",
        "app.master",
        *_OTHER_FINANCE,
    ),
    #  업무 순서와 트랜잭션: HTTP 를 모른다. 마스터 · 화면을 들이지 않는다.
    "service": (
        "fastapi",
        "starlette",
        "app.api",
        "app.master",
        "app.finance.router",
        "app.finance.adapter",
    ),
    #  SQL: 판단 · 조립 · 순서를 부르지 않는다.
    "repository": (
        "app.finance.domain",
        "app.finance.service",
        "app.finance.readmodel",
        "app.finance.llm",
        "app.finance.adapter",
        "app.finance.router",
        "app.master",
        "fastapi",
    ),
    #  조회 조립: 쓰기 순서 · HTTP · 모델을 부르지 않는다.
    "readmodel": (
        "app.finance.service",
        "app.finance.llm",
        "app.finance.adapter",
        "app.finance.router",
        "app.master",
        "fastapi",
        "langgraph",
        "langchain_core",
    ),
    #  모델: 다른 재무 계층을 모른다.
    "schemas": (
        "app.finance.domain",
        *_OTHER_FINANCE,
        "app.core.db",
        "app.master",
        "psycopg",
        "fastapi",
    ),
}

#: domain 이 들이는 재무 밖 앱 모듈의 전부. 늘리려면 이유를 적는다.
_DOMAIN_APP_ALLOWED = (
    "app.finance.domain",
    "app.finance.schemas",
    "app.contracts",
    #  매입 제안의 일정 항목 모양 — 매입 원가 일정(`domain/tools.py`)이 읽는다(종전과 같다).
    "app.purchase_agent.schemas.proposal",
)


def _layer_problems(source: str, forbidden: tuple[str, ...]) -> list[str]:
    return sorted(
        f"{line}: {shape}"
        for line, shape, names in module_refs(source)
        if any(is_under(name, bad) for name in names for bad in forbidden)
    )


@pytest.mark.parametrize("layer", sorted(_FORBIDDEN))
def test_each_layer_imports_only_what_it_may(layer):
    files = _finance_files(layer)
    assert files, f"app/finance/{layer}/ 에 파일이 없다 — 스캐너가 빈 폴더를 본다"
    offenders = {
        _rel(path): problems
        for path in files
        if (problems := _layer_problems(_read(path), _FORBIDDEN[layer]))
    }

    assert offenders == {}, offenders


def test_domain_reaches_outside_finance_only_through_the_allowlist():
    offenders = {
        _rel(path): sorted(
            name
            for name in _imported(_read(path))
            if name.startswith("app.")
            and not any(is_under(name, allowed) for allowed in _DOMAIN_APP_ALLOWED)
        )
        for path in _finance_files("domain")
    }

    assert {path: names for path, names in offenders.items() if names} == {}


@pytest.mark.parametrize(
    ("layer", "planted"),
    [
        ("domain", "def f():\n    from app.core import db\n"),
        ("domain", "import os\n"),
        ("domain", "import importlib\nimportlib.import_module('app.finance.repository.runs')\n"),
        ("service", "from fastapi import HTTPException\n"),
        ("repository", "from app.finance.domain.closing import closing_row\n"),
        ("schemas", "from app.finance.domain.rules import classify_base_stress\n"),
        ("readmodel", "from app.finance.service.expenses import accrue_operating_expense\n"),
    ],
)
def test_the_layer_scan_catches_planted_shapes(layer, planted):
    assert _layer_problems(planted, _FORBIDDEN[layer])


def test_the_domain_does_not_load_db_http_or_llm_at_runtime():
    """import 목록만이 아니라 **실제로 불러와지는 모듈**로도 잰다 (새 파이썬 하나)."""
    domain = sorted(_module_name(path) for path in _finance_files("domain"))
    probe = (
        "import importlib, sys\n"
        f"for name in {domain!r}:\n"
        "    importlib.import_module(name)\n"
        "print('\\n'.join(sorted(sys.modules)))\n"
    )
    loaded = set(
        subprocess.run(
            [sys.executable, "-c", probe],
            cwd=_BACKEND,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
    )

    for heavy in (
        "psycopg",
        "psycopg_pool",
        "fastapi",
        "langgraph",
        "langchain_core",
        "app.core.db",
        "app.finance.repository",
        "app.finance.service",
        "app.finance.readmodel",
        "app.finance.llm",
    ):
        assert heavy not in loaded, heavy


def _code_uses(source: str) -> set[str]:
    """원문이 **코드로** 쓰는 이름 · 속성 · 키워드 인자 (문자열 · 주석 · docstring 은 빼고)."""
    used: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, ast.keyword) and node.arg:
            used.add(node.arg)
    return used


#: SQL 을 짓거나 실행하는 흔적. 연결 **타입**을 읽는 것(`psycopg.Connection`)은 아니다.
#: ★ `execute` 는 뺀다 — 재무 Tool 등록소가 `registry.execute(tool, …)` 로 Tool 을 부른다.
#:   연결에 직접 실행하는 모양(`conn.execute` 등)은 아래 `_executes_on_a_connection` 이 잡는다.
_SQL_USES = frozenset({"executemany", "cursor", "SQL", "Identifier", "Placeholder", "Jsonb"})
_CONNECTION_NAMES = frozenset({"conn", "connection", "cursor", "cur"})


def _executes_on_a_connection(source: str) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"execute", "executemany"}
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in _CONNECTION_NAMES
        for node in ast.walk(ast.parse(source))
    )


def test_sql_runs_only_in_the_repository():
    """🔴 SQL 문과 커서는 `repository/` 에만 있다. 연결을 **빌리는** 것은 readmodel · service 다."""
    offenders = {
        _rel(path): sorted(_code_uses(_read(path)) & _SQL_USES)
        or ["connection.execute"] * _executes_on_a_connection(_read(path))
        for path in _finance_files()
        if "repository" not in path.relative_to(_FINANCE).parts
    }

    assert {path: uses for path, uses in offenders.items() if uses} == {}
    assert all(
        _code_uses(_read(path)) & _SQL_USES
        for path in _finance_files("repository")
        if path.name not in {"__init__.py", "_cursor.py"}
    ), "repository 파일에서 SQL 을 못 찾았다 — 검사가 빈 곳을 본다"


def test_the_sql_scan_catches_planted_statements():
    planted = "def f(conn):\n    with conn.cursor() as c:\n        c.execute(sql.SQL('x'))\n"
    assert _code_uses(planted) & _SQL_USES == {"cursor", "SQL"}
    assert _executes_on_a_connection("def f(conn):\n    conn.execute('SELECT 1')\n")
    assert not _executes_on_a_connection("def f(registry):\n    registry.execute('tool', {})\n")


# ---------------------------------------------------------------------------
# 4. 순환 · private 이름 · 남은 빚
# ---------------------------------------------------------------------------


def _top_level_finance_edges() -> dict[str, set[str]]:
    """재무 모듈마다 **모듈 맨 위**에서 들이는 재무 모듈."""
    modules = {_module_name(path): path for path in _finance_files()}
    edges: dict[str, set[str]] = {name: set() for name in modules}
    for name, path in modules.items():
        for node in ast.parse(_read(path)).body:
            if isinstance(node, ast.ImportFrom) and node.module:
                targets = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
            elif isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            else:
                continue
            edges[name].update(t for t in targets if t in modules and t != name)
    return edges


def _cycles(edges: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan 강연결 요소 중 둘 이상 묶인 것."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[list[str]] = []
    counter = [0]

    def visit(node: str) -> None:
        index[node] = low[node] = counter[0]
        counter[0] += 1
        stack.append(node)
        on_stack.add(node)
        for nxt in edges[node]:
            if nxt not in index:
                visit(nxt)
                low[node] = min(low[node], low[nxt])
            elif nxt in on_stack:
                low[node] = min(low[node], index[nxt])
        if low[node] == index[node]:
            group = []
            while True:
                top = stack.pop()
                on_stack.discard(top)
                group.append(top)
                if top == node:
                    break
            if len(group) > 1:
                found.append(sorted(group))

    for node in edges:
        if node not in index:
            visit(node)
    return found


def test_finance_has_no_import_cycle():
    edges = _top_level_finance_edges()
    assert len(edges) > 100, "재무 모듈을 거의 못 읽었다 — 스캐너가 빈 곳을 본다"

    assert _cycles(edges) == []


def test_the_cycle_finder_sees_a_planted_cycle():
    assert _cycles({"a": {"b"}, "b": {"a"}, "c": set()}) == [["a", "b"]]


#: 함수 안 import — Controller(`service/agent.py`) · Harness(`service/harness.py`) · Planner
#: (`llm/planner.py`) 사이의 순환을 피하던 자리다. 재구성 전부터 있었고(`application/` ·
#: `llm/planner.py`), 표(`CAPABILITY_OWNER`) · 예외(`FinancePlannerContractViolation`)의 자리를
#: 옮기는 일은 LLM 계층화(BL-020)다. **늘지 않게** 지금 모양을 못박는다.
_KNOWN_FUNCTION_IMPORTS = {
    ("app/finance/llm/planner.py", "app.finance.service.harness"),
    ("app/finance/service/agent.py", "app.finance.service.harness"),
    ("app/finance/service/harness.py", "app.finance.llm.planner"),
}


def test_function_level_imports_do_not_grow():
    found = set()
    for path in _finance_files():
        for node in ast.walk(ast.parse(_read(path))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found |= {
                    (_rel(path), inner.module or "")
                    for inner in ast.walk(node)
                    if isinstance(inner, ast.ImportFrom)
                }
                found |= {
                    (_rel(path), alias.name)
                    for inner in ast.walk(node)
                    if isinstance(inner, ast.Import)
                    for alias in inner.names
                }

    assert found == _KNOWN_FUNCTION_IMPORTS


#: `llm/` 안에서 서로 나눠 쓰는 private 이름 — 재구성 전부터 있었다(`llm/client.py`). LLM
#: 계층화(BL-020)에서 공개 이름으로 올린다. **그 밖**에서는 재무 모듈의 `_이름` 을 들이지 않는다.
_KNOWN_LLM_PRIVATE = {
    ("app/finance/llm/finalizer.py", "app.finance.llm.client", "_finance_model"),
    ("app/finance/llm/finalizer.py", "app.finance.llm.client", "_gemini_generate"),
    ("app/finance/llm/planner.py", "app.finance.llm.client", "_DEFAULT_MODELS"),
    ("app/finance/llm/planner.py", "app.finance.llm.client", "_finance_model"),
    ("app/finance/llm/planner.py", "app.finance.llm.client", "_finance_provider_name"),
    ("app/finance/llm/planner.py", "app.finance.llm.client", "_gemini_availability_failure_reason"),
    ("app/finance/llm/planner.py", "app.finance.llm.client", "_gemini_tool_call"),
    ("app/finance/llm/planner.py", "app.finance.llm.client", "_ollama_availability_failure_reason"),
    ("app/finance/llm/planner.py", "app.finance.llm.client", "_ollama_tool_call"),
    ("app/finance/llm/planner.py", "app.finance.llm.client", "_ollama_tool_calling_model"),
}


def test_no_module_imports_a_private_finance_name():
    """다른 모듈의 `_이름` 을 가져다 쓰지 않는다 — 필요하면 공개 이름으로 올린다 (앱 전체)."""
    found = {
        (_rel(path), node.module, alias.name)
        for path in python_files(_APP)
        for node in ast.walk(ast.parse(_read(path)))
        if isinstance(node, ast.ImportFrom)
        and node.module
        and is_under(node.module, "app.finance")
        for alias in node.names
        if alias.name.startswith("_")
    }

    assert found == _KNOWN_LLM_PRIVATE
