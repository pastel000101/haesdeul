"""HTTP 입구의 자리 — 2026-09-30 재구성 BL-019.

```text
app/main.py        app/api/router.py 의 router 하나를 등록한다(풀 수명 · 등록소 조립 · /health · /)
app/api/router.py  모든 라우트 모듈을 종전 순서대로 모은다
                   (화면 탭 `/api/…` · 부서 · 마스터 · Critic · ML)
app/api/<부서>/    라우트(HTTP ↔ service · readmodel) · 화면 탭 presenter(표 · 문장)
부서 · 마스터 폴더  FastAPI 없음 — HTTP 입구는 전부 app/api 에 있다
```

이 파일이 잠그는 것:

1. 부서 · 마스터 · 계약 · 기반 패키지는 FastAPI · Starlette 를 들이지 않는다. 옛 라우터 경로
   여섯은 되살아나지 않는다(재수출 shim 없음).
2. 화면 · HTTP 입구(`app/api`)에는 SQL 도 DB 드라이버도 없다 — `psycopg` 를 들이지 않고, 커서 ·
   SQL 조립 · 조회 헬퍼를 부르지 않고, 연결을 빌리지 않는다. 연결 모듈에서 들이는 것은 두 자리
   이름까지 정해 둔다(재무 HTTP 의 `Depends` 연결 · 물류 화면의 실패 분류). 받은 연결에
   `commit` · `rollback` 도 부르지 않는다 — 트랜잭션을 끝내는 것은 service 다(2026-10-01 BL-022).
3. 라우트 모듈을 들이는 곳은 `app/api/router.py` 하나이고, `app/main.py` 는 그 모음 하나만
   등록한다.
4. 등록된 주소 · 메서드는 옮기기 전(`a2a18fad`)과 같은 순서로 같다 — 74개, 중복 없음.

★ 스캐너는 `import_scan.py` 한 벌이다(함수 안 · 문자열 동적 import 까지 센다). 검사마다 **심어 둔
  모양을 스스로 잡는지**도 잰다 — 0건을 세는 검사가 공짜로 초록이 되지 않게.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest
from fastapi.routing import iter_route_contexts
from import_scan import is_under, module_refs, python_files

import app

_APP = Path(app.__file__).parent
_BACKEND = _APP.parent
_API = _APP / "api"

#: HTTP 를 몰라야 하는 패키지 — 부서 다섯 · 마스터 · 계약 · 기반.
_NO_HTTP = ("finance", "sales", "logistics", "purchase_agent", "ml", "master", "contracts", "core")

#: 2026-09-30 재구성 BL-019 전의 라우터 모듈. `app/api/<부서>/` 로 옮겼고 옛 이름은 남기지 않았다.
_OLD_ROUTERS = (
    "app.finance.router",
    "app.sales.router",
    "app.master.router",
    "app.master.critic.router",
    "app.ml.router",
    "app.ml.console_proxy",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _rel(path: Path) -> str:
    return path.relative_to(_APP).as_posix()


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(_BACKEND).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imported(source: str) -> set[str]:
    return {name for _line, _shape, names in module_refs(source) for name in names}


# ---------------------------------------------------------------------------
# 1. 부서 · 마스터에는 FastAPI 가 없다
# ---------------------------------------------------------------------------


def _takes_http(source: str) -> bool:
    return any(
        is_under(name, top) for name in _imported(source) for top in ("fastapi", "starlette")
    )


def test_departments_and_master_know_no_fastapi() -> None:
    files = [path for package in _NO_HTTP for path in python_files(_APP / package)]
    assert len(files) > 500, f"{len(files)}개만 읽었다 — 스캐너가 폴더를 못 찾는다"

    assert sorted(_rel(path) for path in files if _takes_http(_read(path))) == []


def test_http_scan_catches_planted_imports() -> None:
    assert _takes_http("from fastapi import APIRouter\n")
    assert _takes_http("def f():\n    from starlette.responses import JSONResponse\n")
    assert not _takes_http("# fastapi 를 말만 한다\nx = 'from fastapi import APIRouter'\n")


@pytest.mark.parametrize("module", _OLD_ROUTERS)
def test_the_old_router_modules_are_not_left_behind(module: str) -> None:
    """옛 경로에 재수출 shim 을 두지 않았다 — 부르던 곳은 새 자리로 옮겼다."""
    assert importlib.util.find_spec(module) is None, module


# ---------------------------------------------------------------------------
# 2. app/api 에는 SQL 도 DB 드라이버도 없다
# ---------------------------------------------------------------------------

#: 코드로 쓰면 SQL 을 실행하거나 조립하는 이름(문자열 · 주석 · docstring 은 세지 않는다).
_SQL_USES = frozenset(
    {"execute", "executemany", "cursor", "fetch_one", "fetch_all", "execute_returning_one", "SQL"}
)
#: 연결을 빌리거나 트랜잭션을 여는 연결 모듈 함수 — 화면 · HTTP 입구는 부르지 않는다.
_BORROW = frozenset({"connection", "read_connection", "transaction", "pool_lifespan"})
#: 트랜잭션을 끝내는 이름 — ★ 2026-10-01 재구성 BL-022: 재무 HTTP 는 `Depends` 로 연결을 받으므로
#: 핸들러가 그 연결에 `commit()` · `rollback()` 을 부를 수 있다. 끝내는 자리는 service 다
#: (백로그 BL-022 ② «`app/api/**` 에 `commit(` 없음»).
_TRANSACTION_ENDS = frozenset({"commit", "rollback"})
#: 연결 모듈(`app.core.db`)에서 들여도 되는 것 — 파일 → 쓰는 이름.
_CORE_DB_ALLOWED = {
    #  요청마다 빌린 연결을 `Depends` 로 받는 자리(재무 쓰기 · 여신한도 이력) — 트랜잭션은 service.
    "api/finance/deps.py": frozenset({"Connection", "db_connection"}),
    #  읽기 실패를 503/500 으로 가르는 드라이버 예외 분류 — 연결은 빌리지 않는다.
    "api/logistics/presenter.py": frozenset({"is_unavailable"}),
}


def _code_names(source: str) -> set[str]:
    used: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
    return used


def _core_db_names(source: str) -> set[str]:
    """`app.core.db` 에서 쓰는 이름 — `from app.core.db import X` 와 `core_db.X` 둘 다."""
    tree = ast.parse(source)
    names: set[str] = set()
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.core.db":
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module == "app.core":
            aliases |= {alias.asname or alias.name for alias in node.names if alias.name == "db"}
        elif isinstance(node, ast.Import):
            aliases |= {
                alias.asname for alias in node.names if alias.name == "app.core.db" and alias.asname
            }
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in aliases
        ):
            names.add(node.attr)
    return names


def test_api_holds_no_sql_or_db_driver() -> None:
    files = python_files(_API)
    assert len(files) > 40, f"{len(files)}개만 읽었다 — 스캐너가 app/api 를 못 찾는다"

    drivers = {
        _rel(path)
        for path in files
        if any(
            is_under(name, driver)
            for name in _imported(_read(path))
            for driver in ("psycopg", "psycopg_pool")
        )
    }
    sql = {_rel(path): sorted(_code_names(_read(path)) & _SQL_USES) for path in files}
    ends = {_rel(path): sorted(_code_names(_read(path)) & _TRANSACTION_ENDS) for path in files}

    assert drivers == set()
    assert {path: uses for path, uses in sql.items() if uses} == {}
    assert {path: uses for path, uses in ends.items() if uses} == {}


def test_api_takes_from_the_connection_module_only_the_listed_names() -> None:
    taken = {
        _rel(path): names for path in python_files(_API) if (names := _core_db_names(_read(path)))
    }

    assert taken == _CORE_DB_ALLOWED, taken
    assert not any(names & _BORROW for names in taken.values())


def test_api_scans_catch_planted_shapes() -> None:
    planted_sql = "def f(conn):\n    with conn.cursor() as c:\n        c.execute('x')\n"
    assert _code_names(planted_sql) & _SQL_USES
    assert not _code_names("x = 'conn.cursor() · execute'\n# fetch_all 은 말만 한다\n") & _SQL_USES
    planted_end = "def handler(conn):\n    service.save(conn)\n    conn.commit()\n"
    assert _code_names(planted_end) & _TRANSACTION_ENDS == {"commit"}
    assert not _code_names("# commit 은 service 가 한다\nx = 'rollback'\n") & _TRANSACTION_ENDS
    planted_borrow = (
        "from app.core import db as core_db\nwith core_db.connection() as c:\n    pass\n"
    )
    assert _core_db_names(planted_borrow) == {"connection"}
    assert _core_db_names("from app.core.db import read_connection\n") == {"read_connection"}
    assert _core_db_names("import app.core.db as cdb\ncdb.transaction(c)\n") == {"transaction"}


# ---------------------------------------------------------------------------
# 3. 라우트 모듈을 들이는 곳은 HTTP 입구 목록 하나
# ---------------------------------------------------------------------------


def _defines_router(source: str) -> bool:
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "router" for t in node.targets
        ):
            value = node.value
            if isinstance(value, ast.Call) and getattr(value.func, "id", None) == "APIRouter":
                return True
    return False


def _route_modules() -> list[str]:
    return sorted(
        _module_name(path)
        for path in python_files(_API)
        if path.name != "router.py" and _defines_router(_read(path))
    )


def test_route_modules_are_imported_only_by_the_route_list() -> None:
    modules = _route_modules()
    #  화면 탭 여섯 · 콘솔 조회 셋 · 재무 여섯 · 판매 넷 · 마스터 다섯 · Critic 둘 · ML 둘
    assert len(modules) == 28, modules
    importers = {
        module: sorted(
            _module_name(path)
            for path in python_files(_APP)
            if any(is_under(name, module) for name in _imported(_read(path)))
        )
        for module in modules
    }

    assert {m: who for m, who in importers.items() if who != ["app.api.router"]} == {}


def test_main_registers_only_the_route_list() -> None:
    tree = ast.parse(_read(_APP / "main.py"))
    registered = [
        ast.unparse(node.args[0])
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "include_router"
    ]
    router_imports = {
        f"{node.module}.{alias.name}"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app.api")
        for alias in node.names
    }

    assert registered == ["api_router"]
    assert router_imports == {"app.api.router.router"}


# ---------------------------------------------------------------------------
# 4. 주소 · 메서드 — 옮기기 전과 같다
# ---------------------------------------------------------------------------

#: 옮기기 전(`a2a18fad`)에 등록된 순서 그대로. 프론트가 부르는 주소가 여기 있다 — 바꾸려면 화면
#: (`frontend/src/lib`)과 함께 정하고 이 목록을 같이 고친다.
_ROUTES_BEFORE_BL019 = [
    ("GET", "/api/dashboard"),
    ("GET", "/api/forecast"),
    ("GET", "/api/purchase"),
    ("GET", "/api/finance"),
    ("GET", "/api/logistics"),
    ("GET", "/api/sales"),
    ("GET", "/api/console/finance/summary"),
    ("GET", "/api/console/finance/cashflow"),
    ("GET", "/api/console/finance/credit"),
    ("GET", "/api/console/finance/receivables"),
    ("GET", "/api/console/finance/payables"),
    ("GET", "/api/console/finance/expenses"),
    ("GET", "/api/console/finance/runs"),
    ("GET", "/api/console/finance/runs/latest"),
    ("GET", "/api/console/sales/items"),
    ("GET", "/api/console/sales/summary"),
    ("GET", "/api/console/sales/proposals"),
    ("GET", "/api/console/sales/trend"),
    ("GET", "/api/console/sales/partners"),
    ("GET", "/api/console/sales/partners/{partner_id}"),
    ("GET", "/api/console/sales/collections"),
    ("GET", "/api/console/sales/runs"),
    ("GET", "/api/console/sales/{sale_id}/lifecycle"),
    ("GET", "/api/console/runs"),
    ("GET", "/api/console/shown-run"),
    ("POST", "/ml/qa"),
    ("GET", "/ml/qa"),
    ("GET", "/ml/console/{path:path}"),
    ("POST", "/ml/console/{path:path}"),
    ("GET", "/finance/credit-limits"),
    ("POST", "/finance/credit-limits"),
    ("GET", "/finance/expense-categories"),
    ("POST", "/finance/expenses"),
    ("POST", "/finance/expenses/{expense_id}/settle"),
    ("POST", "/finance/expenses/{expense_id}/cancel"),
    ("POST", "/finance/receivables/collections"),
    ("POST", "/finance/cash-adjustments"),
    ("POST", "/finance/agent"),
    ("GET", "/finance/agent/runs/{run_id}"),
    ("GET", "/finance/runs"),
    ("GET", "/finance/runs/{run_id}"),
    ("POST", "/master/request"),
    ("POST", "/master/sales/run"),
    ("POST", "/master/trigger"),
    ("POST", "/master/ask"),
    ("POST", "/master/ask/execute"),
    ("GET", "/master/runs/{request_id}"),
    ("GET", "/master/runs/{request_id}/report"),
    ("GET", "/master/burn-in"),
    ("GET", "/master/walks/{sim_run_id}/report"),
    ("POST", "/master/runs/{request_id}/decision"),
    ("GET", "/master/runs/{request_id}/commitment"),
    ("POST", "/master/runs/{request_id}/purchase-record"),
    ("GET", "/master/runs/{request_id}/purchase-record"),
    ("GET", "/master/runs/{request_id}/decisions"),
    ("POST", "/master/days/{as_of}/open"),
    ("POST", "/master/days/{as_of}/retry-transitions"),
    ("POST", "/master/days/{as_of}/receive"),
    ("POST", "/master/days/{as_of}/collect"),
    ("POST", "/master/days/{as_of}/issue-receivables"),
    ("POST", "/master/days/{as_of}/ship"),
    ("POST", "/master/days/{as_of}/close"),
    ("POST", "/critic/procurement"),
    ("POST", "/critic/sales"),
    ("GET", "/critic/runs"),
    ("GET", "/critic/runs/{run_id}"),
    ("POST", "/sales/console-proposal"),
    ("POST", "/sales/proposal"),
    ("GET", "/sales/runs"),
    ("GET", "/sales/runs/{run_id}"),
    ("POST", "/sales/partners"),
    ("GET", "/sales/partners/{partner_id}/profile"),
    ("PATCH", "/sales/partners/{partner_id}/profile"),
    ("GET", "/health"),
    ("GET", "/"),
]


def test_registered_routes_are_the_same_as_before_the_move() -> None:
    from app.main import app as fastapi_app

    registered = [
        (method, context.path)
        for context in iter_route_contexts(fastapi_app.routes)
        if getattr(context, "include_in_schema", False)
        for method in sorted(context.methods or ())
    ]

    assert len(set(registered)) == len(registered), "같은 주소 · 메서드가 두 번 등록됐다"
    assert registered == _ROUTES_BEFORE_BL019
