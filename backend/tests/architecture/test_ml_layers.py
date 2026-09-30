"""ML 계층의 방향 — 2026-09-29 재구성 BL-017.

```text
화면   → app/ml/router.py(HTTP 만) → service/qa_graph → domain · readmodel → repository
         app/ml/console_proxy.py(HTTP 만) → ml_backend.py (ML 백엔드 HTTP)
         app/api/forecast(예측 탭) → readmodel/forecast_tab → repository
마스터 → (등록소) → app/ml/adapter.py(번역만) → service/qa_graph (+ 상태 조회는 readmodel)
```

이 파일이 잠그는 것:

1. 질의응답 — 화면 라우트(`/ml/qa`)와 마스터 어댑터가 **같은 service 함수**를 부른다. 라우터
   모듈을 다른 코드가 import 하지 않고, 어댑터를 import 하는 곳은 마스터 등록소 조립 하나다.
2. 계층별로 들일 수 있는 것 — domain 은 DB · HTTP · LLM · 시계 · 그래프를 모르고, service 는 HTTP
   입구를 모르고, schemas 는 다른 계층을 모르고, SQL 은 repository 에만, ML 백엔드 HTTP 는
   `ml_backend.py` 에만 있다. 화면은 ML 에서 조회(readmodel)만 들인다.
3. ML 안에서 순환 · 함수 안 import · 다른 모듈의 private 이름이 없다.

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
_ML = _APP / "ml"
_BACKEND = _APP.parent


def _module_name(path: Path) -> str:
    rel = path.relative_to(_BACKEND).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imported(source: str) -> set[str]:
    return {name for _line, _shape, names in module_refs(source) for name in names}


def _ml_files(layer: str | None = None) -> list[Path]:
    root = _ML if layer is None else _ML / layer
    return python_files(root)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _importers_of(module: str) -> list[str]:
    return sorted(
        _module_name(path)
        for path in python_files(_APP)
        if any(is_under(name, module) for name in _imported(_read(path)))
    )


# ---------------------------------------------------------------------------
# 1. 질의응답 — 화면과 마스터가 같은 service, 입구는 제자리에서만 불린다
# ---------------------------------------------------------------------------


def _bindings(tree: ast.Module) -> dict[str, tuple[str, str]]:
    found: dict[str, tuple[str, str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                found[alias.asname or alias.name] = (node.module, alias.name)
    return found


def _resolved_calls(path: Path, function: str) -> set[tuple[str, str]]:
    tree = ast.parse(_read(path))
    bindings = _bindings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function:
            return {
                bindings[call.func.id]
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id in bindings
            }
    raise AssertionError(f"{function} 이 없다")


_QA_SERVICE = ("app.ml.service.qa_graph", "answer")


@pytest.mark.parametrize(
    ("path", "function"),
    [
        (_ML / "router.py", "ask"),
        (_ML / "router.py", "ask_simple"),
        (_ML / "adapter.py", "ml_port"),
    ],
    ids=["post-qa", "get-qa", "master-port"],
)
def test_qa_entries_go_through_the_one_ml_service(path, function):
    """🔴 화면 `/ml/qa` 와 마스터 `ml_port` 가 **같은 함수 객체**를 부른다."""
    assert _QA_SERVICE in _resolved_calls(path, function)


@pytest.mark.parametrize("router", ["app.ml.router", "app.ml.console_proxy"])
def test_no_module_imports_the_ml_routers_except_the_app_root(router):
    """라우터 핸들러는 HTTP 입구다. 다른 코드가 함수로 부르면 `HTTPException` 이 샌다."""
    assert _importers_of(router) == ["app.main"]


def test_only_the_master_bootstrap_imports_the_ml_adapter():
    # ★ 2026-09-30 재구성 BL-018: 등록소 조립은 `app.master.registry.bootstrap` 이다.
    assert _importers_of("app.ml.adapter") == ["app.master.registry.bootstrap"]


#: 어댑터가 들일 수 있는 것 — 봉투 계약 · 질의응답 service · 상태 조회 · 모델(schemas) ·
#: 고정값(config) · LLM 설정(메타데이터). **SQL · 연결 · ML 백엔드 HTTP · repository 는 없다.**
_ADAPTER_ALLOWED = (
    "app.contracts.core",
    "app.contracts.envelope",
    "app.ml.config",
    "app.ml.llm.qa",
    "app.ml.readmodel.qa_reads",
    "app.ml.schemas",
    "app.ml.service.qa_graph",
)


def test_the_adapter_only_translates():
    source = _read(_ML / "adapter.py")
    imported = {name for name in _imported(source) if name.startswith("app.")}
    offenders = sorted(
        name
        for name in imported
        #  `from app.ml.llm import qa` 는 패키지 `app.ml.llm` 도 함께 낸다 — 허용한 모듈의
        #  윗 패키지는 뺀다
        if not any(
            is_under(name, allowed) or is_under(allowed, name) for allowed in _ADAPTER_ALLOWED
        )
    )

    assert offenders == [], offenders
    assert "psycopg" not in source and "httpx" not in source


# ---------------------------------------------------------------------------
# 2. 계층별로 들일 수 있는 것
# ---------------------------------------------------------------------------

_HTTP_ENTRIES = ("app.ml.router", "app.ml.console_proxy", "app.ml.adapter")

#: 계층마다 **들이면 안 되는** 모듈. 표준 라이브러리 · pydantic 은 막지 않는다.
_FORBIDDEN: dict[str, tuple[str, ...]] = {
    #  판단·계산: 입력 → 출력만.
    "domain": (
        "psycopg",
        "psycopg_pool",
        "httpx",
        "fastapi",
        "starlette",
        "langgraph",
        "urllib.request",
        "dotenv",
        "app.core.db",
        "app.core.settings",
        "app.core.clock",
        "app.api",
        "app.master",
        "app.ml.service",
        "app.ml.repository",
        "app.ml.readmodel",
        "app.ml.llm",
        "app.ml.ml_backend",
        *_HTTP_ENTRIES,
    ),
    #  업무 순서: HTTP 입구를 모른다.
    "service": ("fastapi", "starlette", "app.api", *_HTTP_ENTRIES),
    #  SQL: 판단 · 조립 · 순서 · 외부 HTTP 를 부르지 않는다.
    "repository": (
        "app.ml.domain",
        "app.ml.service",
        "app.ml.readmodel",
        "app.ml.llm",
        "app.ml.ml_backend",
        "httpx",
        "fastapi",
        *_HTTP_ENTRIES,
    ),
    #  조회 조립: 순서 · 해석기 · 외부 HTTP · 그래프를 부르지 않는다.
    "readmodel": (
        "app.ml.service",
        "app.ml.llm",
        "app.ml.ml_backend",
        "httpx",
        "fastapi",
        "langgraph",
        "app.core.clock",
        *_HTTP_ENTRIES,
    ),
    #  모델: 다른 ML 계층을 모른다.
    "schemas": (
        "app.ml.domain",
        "app.ml.service",
        "app.ml.repository",
        "app.ml.readmodel",
        "app.ml.llm",
        "app.ml.ml_backend",
        "app.core.db",
        "psycopg",
        "httpx",
        "fastapi",
        *_HTTP_ENTRIES,
    ),
    #  질문 해석: 프롬프트 · 응답 스키마 · 호출. DB · 그래프 · 다른 계층을 모른다.
    "llm": (
        "app.ml.domain",
        "app.ml.service",
        "app.ml.repository",
        "app.ml.readmodel",
        "app.ml.ml_backend",
        "app.core.db",
        "psycopg",
        "fastapi",
        *_HTTP_ENTRIES,
    ),
}

#: domain 이 들이는 ML 밖 앱 모듈의 전부. 늘리려면 이유를 적는다.
_DOMAIN_APP_ALLOWED = (
    "app.ml.domain",
    "app.ml.schemas",
    "app.ml.config",
    "app.contracts",
    "app.core.text",
)


def _layer_problems(source: str, forbidden: tuple[str, ...]) -> list[str]:
    return sorted(
        f"{line}: {shape}"
        for line, shape, names in module_refs(source)
        if any(is_under(name, bad) for name in names for bad in forbidden)
    )


@pytest.mark.parametrize("layer", sorted(_FORBIDDEN))
def test_each_layer_imports_only_what_it_may(layer):
    files = _ml_files(layer)
    assert files, f"app/ml/{layer}/ 에 파일이 없다 — 스캐너가 빈 폴더를 본다"
    offenders = {
        str(path.relative_to(_BACKEND)): problems
        for path in files
        if (problems := _layer_problems(_read(path), _FORBIDDEN[layer]))
    }

    assert offenders == {}, offenders


def test_domain_reaches_outside_ml_only_through_the_allowlist():
    offenders = {
        str(path.relative_to(_BACKEND)): sorted(
            name
            for name in _imported(_read(path))
            if name.startswith("app.")
            and not any(is_under(name, allowed) for allowed in _DOMAIN_APP_ALLOWED)
        )
        for path in _ml_files("domain")
    }

    assert {path: names for path, names in offenders.items() if names} == {}


@pytest.mark.parametrize(
    ("layer", "planted"),
    [
        ("domain", "def f():\n    from app.core import db\n"),
        ("domain", "from app.core.clock import today_in_seoul\n"),
        ("domain", "import importlib\nimportlib.import_module('app.ml.repository.qa')\n"),
        ("service", "from fastapi import HTTPException\n"),
        ("repository", "import httpx\n"),
        ("readmodel", "from app.ml.service.qa_graph import answer\n"),
        ("schemas", "from app.ml.readmodel import qa_reads\n"),
        ("llm", "from app.core import db\n"),
    ],
)
def test_the_layer_scan_catches_planted_shapes(layer, planted):
    assert _layer_problems(planted, _FORBIDDEN[layer])


def test_the_domain_does_not_load_db_http_llm_or_the_clock_at_runtime():
    """import 목록만이 아니라 **실제로 불러와지는 모듈**로도 잰다 (새 파이썬 하나)."""
    domain = sorted(_module_name(path) for path in _ml_files("domain"))
    probe = (
        "import importlib, sys\n"
        f"for name in {domain!r}:\n"
        "    importlib.import_module(name)\n"
        "print('\\n'.join(sorted(sys.modules)))\n"
    )
    loaded = set(
        subprocess.run(
            [sys.executable, "-c", probe], cwd=_BACKEND, capture_output=True, text=True, check=True
        ).stdout.split()
    )

    for heavy in (
        "psycopg",
        "psycopg_pool",
        "httpx",
        "fastapi",
        "langgraph",
        "urllib.request",
        "app.core.db",
        "app.core.clock",
        "app.ml.llm.qa",
        "app.ml.repository",
        "app.ml.service",
    ):
        assert heavy not in loaded, heavy


def _code_uses(source: str) -> set[str]:
    """원문이 **코드로** 쓰는 이름 · 속성 (문자열 · 주석 · docstring 은 빼고)."""
    used: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
    return used


_SQL_USES = frozenset({"execute", "executemany", "cursor", "SQL", "Identifier", "Placeholder"})


def test_sql_runs_only_in_the_repository():
    """🔴 SQL 문과 커서는 `repository/` 에만 있다. 연결을 **빌리는** 것은 readmodel · service 다."""
    offenders = {
        str(path.relative_to(_BACKEND)): sorted(_code_uses(_read(path)) & _SQL_USES)
        for path in _ml_files()
        if "repository" not in path.relative_to(_ML).parts
    }

    assert {path: uses for path, uses in offenders.items() if uses} == {}
    assert all(
        _code_uses(_read(path)) & _SQL_USES
        for path in _ml_files("repository")
        if path.name != "__init__.py"
    ), "repository 파일에서 SQL 을 못 찾았다 — 검사가 빈 곳을 본다"


def test_the_sql_scan_catches_a_planted_statement():
    planted = "def f(conn):\n    with conn.cursor() as c:\n        c.executemany('x', [])\n"
    assert _code_uses(planted) & _SQL_USES == {"cursor", "executemany"}


def test_readmodels_only_borrow_read_connections():
    """조회는 쓰기 연결 · 트랜잭션을 열지 않는다. 쓰기(적재)의 경계는 service 가 쥔다."""
    offenders = {
        str(path.relative_to(_BACKEND))
        for path in _ml_files("readmodel")
        if "core_db.connection(" in _read(path) or "core_db.transaction(" in _read(path)
    }

    assert offenders == set()
    assert any("read_connection(" in _read(path) for path in _ml_files("readmodel"))


def test_ml_backend_http_lives_in_one_file():
    """🔴 ML 백엔드에 묻는 HTTP 는 `ml_backend.py` 하나 — 라우트 · SQL · 그래프에 흩지 않는다."""
    offenders = sorted(
        str(path.relative_to(_BACKEND))
        for path in _ml_files()
        if any(is_under(name, "httpx") for name in _imported(_read(path)))
    )

    assert offenders == [str((_ML / "ml_backend.py").relative_to(_BACKEND))]
    assert "fastapi" not in {n.split(".")[0] for n in _imported(_read(_ML / "ml_backend.py"))}


def test_the_forecast_screen_takes_only_ml_reads():
    """화면 예측 탭은 ML 에서 조회(readmodel)만 들인다 — SQL · 연결 헬퍼를 직접 다루지 않는다."""
    screen = _APP / "api" / "forecast" / "query.py"
    ml_imports = {name for name in _imported(_read(screen)) if is_under(name, "app.ml")}

    assert ml_imports, "화면이 ML 조회를 안 들인다 — 스캐너가 빈 곳을 본다"
    assert all(
        is_under(name, "app.ml.readmodel") or name == "app.ml"
        for name in ml_imports
    ), ml_imports
    assert not _code_uses(_read(screen)) & _SQL_USES


# ---------------------------------------------------------------------------
# 3. 순환 · 함수 안 import · private 이름
# ---------------------------------------------------------------------------


def _top_level_ml_edges() -> dict[str, set[str]]:
    modules = {_module_name(path): path for path in _ml_files()}
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


def test_ml_has_no_import_cycle():
    edges = _top_level_ml_edges()
    assert len(edges) > 25, "ML 모듈을 거의 못 읽었다 — 스캐너가 빈 곳을 본다"

    assert _cycles(edges) == []


def test_the_cycle_finder_sees_a_planted_cycle():
    assert _cycles({"a": {"b"}, "b": {"a"}, "c": set()}) == [["a", "b"]]


def test_ml_has_no_function_level_import():
    """🔴 순환을 피하려고 import 를 함수 안으로 숨기지 않는다."""
    offenders = []
    for path in _ml_files():
        for node in ast.walk(ast.parse(_read(path))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                offenders += [
                    f"{path.relative_to(_BACKEND)}:{inner.lineno}"
                    for inner in ast.walk(node)
                    if isinstance(inner, (ast.Import, ast.ImportFrom))
                ]

    assert offenders == []


def test_no_module_imports_a_private_ml_name():
    """다른 모듈의 `_이름` 을 가져다 쓰지 않는다 — 필요하면 공개 이름으로 올린다."""
    offenders = [
        f"{path.relative_to(_BACKEND)}: from {node.module} import {alias.name}"
        for path in python_files(_APP)
        for node in ast.walk(ast.parse(_read(path)))
        if isinstance(node, ast.ImportFrom)
        and node.module
        and is_under(node.module, "app.ml")
        for alias in node.names
        if alias.name.startswith("_")
    ]
    private_attrs = [
        f"{path.relative_to(_BACKEND)}: {node.value.id}.{node.attr}"
        for path in _ml_files()
        for node in ast.walk(ast.parse(_read(path)))
        if isinstance(node, ast.Attribute)
        and node.attr.startswith("_")
        and not node.attr.startswith("__")
        and isinstance(node.value, ast.Name)
        and node.value.id in {"qa_llm", "qa_reads", "qa_answer", "qa_scope", "qa_question",
                              "config", "ml_backend", "qa_sql", "forecasts_sql", "tab_sql"}
    ]

    assert offenders == []
    assert private_attrs == []
