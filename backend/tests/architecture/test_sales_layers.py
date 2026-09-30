"""판매 계층의 방향 — 2026-09-29 재구성 BL-013.

```text
화면   → app/api/sales/<자원>.py(HTTP 만) → service · readmodel → domain · repository
         (2026-09-30 재구성 BL-019 전에는 app/sales/router.py 한 파일)
마스터 → (등록소) → app/sales/adapter.py(번역만) → service · readmodel → domain · repository
```

이 파일이 잠그는 것:

1. 거래처 쓰기 — 판매 라우터와 마스터 ask 가 **같은 service 함수**를 부르고, 라우터 함수를
   다른 파이썬 코드가 import 하지 않는다.
2. 판매 후보 생성 — 운영 화면 라우트와 마스터 어댑터가 **같은 service 함수**를 부르고,
   라우터는 어댑터를 부르지 않는다. 어댑터에는 SQL · 연결 · 그래프 실행이 없다.
3. 계층별로 들일 수 있는 것 — domain 은 DB · HTTP · LLM · 그래프를 모르고, service 는 HTTP 를
   모르고, schemas 는 다른 계층을 모르고, SQL 은 repository 에만 있다.
4. 판매 안에서 순환 · 함수 안 import · 다른 모듈의 private 이름이 없다.

★ 스캐너는 `import_scan.py` 한 벌이다(함수 안 · 문자열 동적 import 까지 센다). 검사마다
  **심어 둔 모양을 스스로 잡는지**도 잰다 — 0건을 세는 검사가 공짜로 초록이 되지 않게.
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
_SALES = _APP / "sales"
_BACKEND = _APP.parent


def _module_name(path: Path) -> str:
    rel = path.relative_to(_BACKEND).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imported(source: str) -> set[str]:
    """그 원문이 들이는 모듈 이름 전부 (함수 안 · 동적 import 포함)."""
    return {name for _line, _shape, names in module_refs(source) for name in names}


def _sales_files(layer: str | None = None) -> list[Path]:
    root = _SALES if layer is None else _SALES / layer
    return python_files(root)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. 거래처 쓰기 — 라우터와 마스터 ask 가 같은 service 를 부른다
# ---------------------------------------------------------------------------


def _bindings(tree: ast.Module) -> dict[str, tuple[str, str]]:
    """모듈 맨 위 `from X import Y [as Z]` → `{Z: (X, Y)}`."""
    found: dict[str, tuple[str, str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                found[alias.asname or alias.name] = (node.module, alias.name)
    return found


def _called_in(tree: ast.Module, function: str) -> set[str]:
    """그 함수 본문이 이름으로 부르는 것."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function:
            return {
                call.func.id
                for call in ast.walk(node)
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            }
    raise AssertionError(f"{function} 이 없다")


def _resolved_calls(path: Path, function: str) -> set[tuple[str, str]]:
    """그 함수가 부르는 이름을 **어디서 온 무엇인지**로 푼다."""
    tree = ast.parse(_read(path))
    bindings = _bindings(tree)
    return {bindings[name] for name in _called_in(tree, function) if name in bindings}


_PARTNERS_SERVICE = "app.sales.service.partners"
#: 판매 HTTP 입구 — 2026-09-30 재구성 BL-019 에 `app/sales/router.py` 에서 자원별 파일로 옮겼다.
_HTTP = _APP / "api" / "sales"
_HTTP_MODULES = tuple(
    f"app.api.sales.{name}" for name in ("console_proposal", "proposal", "runs", "partners")
)
#: ★ 2026-09-30 재구성 BL-018: 채팅의 도메인 행동(조회 · 쓰기 실행)은 `master/service/ask.py` 에서
#:   `master/service/ask_domain_actions.py` 로 갈라 나왔다 — `_domain_write` 가 그 파일에 있다.
_ASK = _APP / "master" / "service" / "ask_domain_actions.py"


@pytest.mark.parametrize(
    ("path", "function", "expected"),
    [
        (_HTTP / "partners.py", "add_partner_profile", (_PARTNERS_SERVICE, "create_partner")),
        (_HTTP / "partners.py", "edit_partner_profile", (_PARTNERS_SERVICE, "update_partner")),
        (_ASK, "_domain_write", (_PARTNERS_SERVICE, "create_partner")),
        (_ASK, "_domain_write", (_PARTNERS_SERVICE, "update_partner")),
    ],
    ids=["router-create", "router-update", "ask-create", "ask-update"],
)
def test_partner_writes_go_through_the_one_sales_service(path, function, expected):
    """🔴 판매 라우터와 마스터 ask 가 **같은 함수 객체**를 부른다 — 한쪽만 고쳐지는 날이 없다."""
    assert expected in _resolved_calls(path, function), _resolved_calls(path, function)


def test_no_module_imports_the_sales_router_except_the_app_root():
    """🔴 라우터 핸들러는 HTTP 입구다. 다른 코드가 함수로 부르면 `HTTPException` 이 샌다."""
    importers = sorted(
        _module_name(path)
        for path in python_files(_APP)
        if any(
            is_under(name, module)
            for name in _imported(_read(path))
            for module in _HTTP_MODULES
        )
    )

    #  HTTP 입구 목록(`app/api/router.py`)이 라우터를 등록하는 것 하나뿐이다(2026-09-30 재구성
    #  BL-019 — 라우트는 `app/api/sales/<자원>.py` · 전에는 `main.py` 가
    #  `app.sales.router` 를 들였다).
    assert importers == ["app.api.router"]


def test_the_router_scan_catches_a_planted_handler_import():
    planted = "def f():\n    from app.api.sales.partners import add_partner_profile\n"
    assert any(is_under(name, "app.api.sales.partners") for name in _imported(planted))


# ---------------------------------------------------------------------------
# 2. 판매 후보 생성 — 화면과 마스터가 같은 service, 어댑터는 번역만
# ---------------------------------------------------------------------------

_GENERATION = ("app.sales.service.proposal_generation", "generate_sales_proposal")


@pytest.mark.parametrize(
    ("path", "function"),
    [
        (_HTTP / "console_proposal.py", "create_console_sales_proposal"),
        (_SALES / "adapter.py", "_generate"),
    ],
    ids=["console-route", "adapter"],
)
def test_candidate_generation_goes_through_the_one_sales_service(path, function):
    assert _GENERATION in _resolved_calls(path, function)


def test_the_router_does_not_call_the_master_adapter():
    """🔴 `POST /sales/console-proposal` 이 `sales_port` 를 부르던 길이 없다."""
    assert not {
        n
        for path in _HTTP.glob("*.py")
        for n in _imported(_read(path))
        if is_under(n, "app.sales.adapter")
    }


def test_only_the_master_bootstrap_imports_the_sales_adapter():
    importers = sorted(
        _module_name(path)
        for path in python_files(_APP)
        if any(is_under(name, "app.sales.adapter") for name in _imported(_read(path)))
    )

    # ★ 2026-09-30 재구성 BL-018: 등록소 조립은 `app.master.registry.bootstrap` 이다.
    assert importers == ["app.master.registry.bootstrap"]


#: 어댑터가 들일 수 있는 것 — 봉투 계약 · 판매 판단(회신 어휘) · 조회 · 후보 생성 service ·
#: 모델 설정(메타데이터). **SQL · 연결 · 그래프 실행 · repository 는 없다.**
_ADAPTER_ALLOWED = (
    "app.contracts.envelope",
    "app.sales.domain",
    "app.sales.llm.runtime",
    "app.sales.readmodel.status",
    "app.sales.schemas",
    "app.sales.service.proposal_generation",
)


def test_the_adapter_only_translates():
    imported = {name for name in _imported(_read(_SALES / "adapter.py")) if name.startswith("app.")}
    offenders = sorted(
        name
        for name in imported
        if not any(is_under(name, allowed) for allowed in _ADAPTER_ALLOWED)
    )

    assert offenders == [], offenders
    assert "psycopg" not in _read(_SALES / "adapter.py")


# ---------------------------------------------------------------------------
# 3. 계층별로 들일 수 있는 것
# ---------------------------------------------------------------------------

#: 계층마다 **들이면 안 되는** 모듈. 표준 라이브러리 · pydantic 은 막지 않는다.
_FORBIDDEN: dict[str, tuple[str, ...]] = {
    #  판단·계산: 입력 → 출력만. 정책값은 재무가 상수로 낸 `app.finance.domain.sales_policy` 하나
    #  (2026-09-29 재구성 BL-014 전에는 `app.finance.sales_policy`).
    "domain": (
        "psycopg",
        "psycopg_pool",
        "fastapi",
        "starlette",
        "langgraph",
        "urllib",
        "dotenv",
        "app.core.db",
        "app.core.settings",
        "app.api",
        "app.master",
        "app.sales.service",
        "app.sales.repository",
        "app.sales.readmodel",
        "app.sales.llm",
        "app.sales.adapter",
    ),
    #  업무 순서와 트랜잭션: HTTP 를 모른다.
    "service": ("fastapi", "starlette", "app.api", "app.sales.adapter"),
    #  SQL: 판단 · 조립 · 순서를 부르지 않는다.
    "repository": (
        "app.sales.domain",
        "app.sales.service",
        "app.sales.readmodel",
        "app.sales.llm",
        "app.sales.adapter",
        "app.api",
        "fastapi",
    ),
    #  조회 조립: 쓰기 순서 · HTTP · 모델을 부르지 않는다.
    "readmodel": (
        "app.sales.service",
        "app.sales.llm",
        "app.sales.adapter",
        "app.api",
        "fastapi",
        "langgraph",
    ),
    #  모델: 다른 판매 계층을 모른다.
    "schemas": (
        "app.sales.domain",
        "app.sales.service",
        "app.sales.repository",
        "app.sales.readmodel",
        "app.sales.llm",
        "app.sales.adapter",
        "app.api",
        "app.core.db",
        "psycopg",
        "fastapi",
    ),
}

#: domain 이 들이는 판매 밖 앱 모듈의 전부. 늘리려면 이유를 적는다.
_DOMAIN_APP_ALLOWED = (
    "app.sales.domain",
    "app.sales.schemas",
    "app.contracts",
    "app.core.text",
    #  판매 → 재무 import 는 정책 상수 하나 (`test_sales_never_imports_finance_runtime`).
    "app.finance.domain.sales_policy",
)


def _layer_problems(source: str, forbidden: tuple[str, ...]) -> list[str]:
    return sorted(
        f"{line}: {shape}"
        for line, shape, names in module_refs(source)
        if any(is_under(name, bad) for name in names for bad in forbidden)
    )


@pytest.mark.parametrize("layer", sorted(_FORBIDDEN))
def test_each_layer_imports_only_what_it_may(layer):
    files = _sales_files(layer)
    assert files, f"app/sales/{layer}/ 에 파일이 없다 — 스캐너가 빈 폴더를 본다"
    offenders = {
        str(path.relative_to(_BACKEND)): problems
        for path in files
        if (problems := _layer_problems(_read(path), _FORBIDDEN[layer]))
    }

    assert offenders == {}, offenders


def test_domain_reaches_outside_sales_only_through_the_allowlist():
    offenders = {
        str(path.relative_to(_BACKEND)): sorted(
            name
            for name in _imported(_read(path))
            if name.startswith("app.")
            and not any(is_under(name, allowed) for allowed in _DOMAIN_APP_ALLOWED)
        )
        for path in _sales_files("domain")
    }

    assert {path: names for path, names in offenders.items() if names} == {}


@pytest.mark.parametrize(
    ("layer", "planted"),
    [
        ("domain", "def f():\n    from app.core import db\n"),
        ("domain", "import importlib\nimportlib.import_module('app.sales.repository.runs')\n"),
        ("service", "from fastapi import HTTPException\n"),
        ("repository", "from app.sales.domain.proposal import validate_context\n"),
        ("schemas", "from app.sales.service.partners import create_partner\n"),
        ("readmodel", "from app.sales.service.proposal import run_proposal\n"),
    ],
)
def test_the_layer_scan_catches_planted_shapes(layer, planted):
    assert _layer_problems(planted, _FORBIDDEN[layer])


def test_the_domain_does_not_load_db_http_or_llm_at_runtime():
    """import 목록만이 아니라 **실제로 불러와지는 모듈**로도 잰다 (새 파이썬 하나)."""
    domain = sorted(_module_name(path) for path in _sales_files("domain"))
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
        "app.core.db",
        "app.sales.llm.runtime",
        "app.sales.repository",
        "app.sales.service",
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
_SQL_USES = frozenset({"execute", "executemany", "cursor", "SQL", "Identifier", "Placeholder"})


def test_sql_runs_only_in_the_repository():
    """🔴 SQL 문과 커서는 `repository/` 에만 있다. 연결을 **빌리는** 것은 readmodel · service 다."""
    offenders = {
        str(path.relative_to(_BACKEND)): sorted(_code_uses(_read(path)) & _SQL_USES)
        for path in _sales_files()
        if "repository" not in path.relative_to(_SALES).parts
    }

    assert {path: uses for path, uses in offenders.items() if uses} == {}
    assert all(
        _code_uses(_read(path)) & _SQL_USES
        for path in _sales_files("repository")
        if path.name not in {"__init__.py"}
    ), "repository 파일에서 SQL 을 못 찾았다 — 검사가 빈 곳을 본다"


def test_the_sql_scan_catches_a_planted_statement():
    planted = "def f(conn):\n    with conn.cursor() as c:\n        c.execute(sql.SQL('x'))\n"
    assert _code_uses(planted) & _SQL_USES == {"cursor", "execute", "SQL"}


def test_readmodels_only_borrow_read_connections():
    """조회는 쓰기 연결 · 트랜잭션을 열지 않는다 — 열면 화면이 장부를 바꿀 길이 생긴다."""
    offenders = {
        str(path.relative_to(_BACKEND))
        for path in _sales_files("readmodel")
        if "core_db.connection(" in _read(path) or "core_db.transaction(" in _read(path)
    }

    assert offenders == set()
    assert any("core_db.read_connection(" in _read(path) for path in _sales_files("readmodel"))


def test_the_service_decides_no_http_status():
    """🔴 상태 코드를 정하는 것은 라우터다. service 는 업무 결과 · 업무 예외만 낸다."""
    offenders = {
        str(path.relative_to(_BACKEND))
        for path in _sales_files("service")
        if _code_uses(_read(path)) & {"HTTPException", "status_code"}
    }

    assert offenders == set()
    assert _code_uses("raise HTTPException(status_code=409)") >= {"HTTPException", "status_code"}


# ---------------------------------------------------------------------------
# 4. 순환 · 함수 안 import · private 이름
# ---------------------------------------------------------------------------


def _top_level_sales_edges() -> dict[str, set[str]]:
    """판매 모듈마다 **모듈 맨 위**에서 들이는 판매 모듈 (`if TYPE_CHECKING:` 은 뺀다)."""
    modules = {_module_name(path): path for path in _sales_files()}
    edges: dict[str, set[str]] = {name: set() for name in modules}
    for name, path in modules.items():
        tree = ast.parse(_read(path))
        for node in tree.body:
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


def test_sales_has_no_import_cycle():
    """🔴 `proposal ⇄ graph` · `strategy ⇄ llm.runtime` 가 함수 안 import 로 피하던 고리였다."""
    edges = _top_level_sales_edges()
    assert len(edges) > 40, "판매 모듈을 거의 못 읽었다 — 스캐너가 빈 곳을 본다"

    assert _cycles(edges) == []


def test_the_cycle_finder_sees_a_planted_cycle():
    assert _cycles({"a": {"b"}, "b": {"a"}, "c": set()}) == [["a", "b"]]


def test_sales_has_no_function_level_import():
    """🔴 순환을 피하려고 import 를 함수 안으로 숨기지 않는다 (무거운 SDK 도 판매에는 없다)."""
    offenders = []
    for path in _sales_files():
        for node in ast.walk(ast.parse(_read(path))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                offenders += [
                    f"{path.relative_to(_BACKEND)}:{inner.lineno}"
                    for inner in ast.walk(node)
                    if isinstance(inner, (ast.Import, ast.ImportFrom))
                ]

    assert offenders == []


def test_no_sales_module_imports_another_modules_private_name():
    """다른 모듈의 `_이름` 을 가져다 쓰지 않는다 — 필요하면 공개 이름으로 올린다."""
    offenders = [
        f"{path.relative_to(_BACKEND)}: from {node.module} import {alias.name}"
        for path in python_files(_APP)
        for node in ast.walk(ast.parse(_read(path)))
        if isinstance(node, ast.ImportFrom)
        and node.module
        and is_under(node.module, "app.sales")
        for alias in node.names
        if alias.name.startswith("_")
    ]

    assert offenders == []
