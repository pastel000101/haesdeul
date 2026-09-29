"""매입 계층의 방향 — 2026-09-29 재구성 BL-016.

```text
마스터 → (등록소) → app/purchase_agent/adapter.py(번역만)
             ├ GENERATE_SCENARIOS    → domain/payload(수신 검사) → service/scenarios → service/graph
             │                          → service/nodes/* → domain/* (판정 · 계산)
             └ SUPPLY_CAPACITY_QUERY → service/supply_capacity → domain/supply_capacity
시세 한 가지만 직접 읽는다:
  ports.get_market_quotes → readmodel/quotes(조회 연결) → repository/quotes(SQL)
```

이 파일이 잠그는 것:

1. 들어오는 길 — 어댑터를 import 하는 곳은 마스터 등록소 조립 하나이고, 어댑터는 번역만 한다
   (시나리오 생성 · 공급 가능량은 service 함수를 부른다).
2. 계층별로 들일 수 있는 것 — domain 은 DB · HTTP · LLM 호출 · 시계 · 기능 플래그 · 포트를 모르고,
   부서 선언(`constraints.yaml`)도 읽지 않는다 — 부르는 쪽(service 노드 · 어댑터)이 읽어 넘긴다.
   service 는 HTTP 입구를 모르고, schemas 는 다른 계층을 모르고, SQL 은 repository 에만 있다.
   노드 함수는 `service/nodes/` 에 있다.
3. 매입은 **읽기 전용**이다 (규칙 2) — 쓰기 SQL · commit · 쓰기 대여가 없고, 조회는 readmodel 이
   조회 연결로만 빌린다.
4. 매입 안에서 순환 · 함수 안 import · 다른 모듈의 private 이름이 없다.

★ 스캐너는 `import_scan.py` 한 벌이다(함수 안 · 문자열 동적 import 까지 센다). 검사마다
  **심어 둔 모양을 스스로 잡는지**도 잰다 — 0건을 세는 검사가 공짜로 초록이 되지 않게.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest
from import_scan import is_under, module_refs, python_files

import app

_APP = Path(app.__file__).parent
_PA = _APP / "purchase_agent"
_BACKEND = _APP.parent
_PKG = "app.purchase_agent"


def _module_name(path: Path) -> str:
    rel = path.relative_to(_BACKEND).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imported(source: str) -> set[str]:
    return {name for _line, _shape, names in module_refs(source) for name in names}


def _files(layer: str | None = None) -> list[Path]:
    return python_files(_PA if layer is None else _PA / layer)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _importers_of(module: str) -> list[str]:
    return sorted(
        _module_name(path)
        for path in python_files(_APP)
        if any(is_under(name, module) for name in _imported(_read(path)))
    )


# ---------------------------------------------------------------------------
# 1. 들어오는 길 — 마스터 등록소 → 어댑터(번역) → service
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


def test_only_the_master_bootstrap_imports_the_purchase_adapter():
    assert _importers_of(f"{_PKG}.adapter") == ["app.master.bootstrap"]


@pytest.mark.parametrize(
    ("function", "service"),
    [
        ("_generate_scenarios", (f"{_PKG}.service.scenarios", "generate_scenarios")),
        ("_supply_capacity_query", (f"{_PKG}.service.supply_capacity", "read_supply_capacity")),
    ],
    ids=["generate-scenarios", "supply-capacity"],
)
def test_the_adapter_hands_execution_to_the_service(function, service):
    """🔴 어댑터는 그래프를 조립하거나 시세를 읽지 않는다 — 실행 순서는 service 가 쥔다."""
    assert service in _resolved_calls(_PA / "adapter.py", function)


#: 어댑터가 들일 수 있는 것 — 봉투 계약 · 매입 service · 회신 문장 · 수신 검사(domain) · 모델 ·
#: 부서 선언 · LLM 설정(실행 흔적의 상태 · 판) · 시세 공급자 타입.
#: ⚠️ `mocks` 는 상태 조회가 답하는 품목 목록(`mocks.ITEMS`) 하나 때문이다 — 재구성 전부터의
#:   배선이고 값은 계약 품목과 같다. 계약 품목으로 바꿀지는 확인 필요(BL-016 §연결).
#: **SQL · 연결 · 그래프 조립 · repository · 포트 호출은 없다.**
_ADAPTER_ALLOWED = (
    "app.contracts.core",
    "app.contracts.envelope",
    f"{_PKG}.config",
    f"{_PKG}.mocks",
    f"{_PKG}.domain.evidence",
    f"{_PKG}.domain.payload",
    f"{_PKG}.domain.quotes",
    f"{_PKG}.domain.reasoning",
    f"{_PKG}.domain.supply_capacity",
    f"{_PKG}.llm.runtime",
    f"{_PKG}.llm.split_allocation",
    f"{_PKG}.readmodel.quotes",
    f"{_PKG}.service.scenarios",
    f"{_PKG}.service.supply_capacity",
    f"{_PKG}.service.tracing",
)


def test_the_adapter_only_translates():
    source = _read(_PA / "adapter.py")
    imported = {name for name in _imported(source) if name.startswith("app.")}
    offenders = sorted(
        name
        for name in imported
        #  `from app.purchase_agent import AGENT_VERSION, mocks` 는 패키지 자신도 낸다 —
        #  허용한 모듈의 윗 패키지는 뺀다
        if not any(
            is_under(name, allowed) or is_under(allowed, name) for allowed in _ADAPTER_ALLOWED
        )
        and name != f"{_PKG}.AGENT_VERSION"
    )

    assert offenders == [], offenders
    #  그래프 조립 · 포트 호출 · SQL · 연결 대여를 코드로 쓰지 않는다 (설명문의 낱말은 안 센다).
    assert not _code_uses(source) & {
        "build_graph", "get_market_quotes", "ports", "cursor", "execute", "read_connection",
        "fetch_rows",
    }
    assert "psycopg" not in source


# ---------------------------------------------------------------------------
# 2. 계층별로 들일 수 있는 것
# ---------------------------------------------------------------------------

_HTTP = ("fastapi", "starlette", "app.api")
#: LLM 을 **부르는** 매입 모듈. 모델(`llm.schemas` · `split_schemas` · `review_schemas`)과
#: 문자 검사(`llm.text_guard`)는 입력 → 출력이라 domain 이 쓸 수 있다.
_LLM_CALLERS = (
    f"{_PKG}.llm.runtime",
    f"{_PKG}.llm.mix",
    f"{_PKG}.llm.self_review",
    f"{_PKG}.llm.split_allocation",
)

#: 계층마다 **들이면 안 되는** 모듈. 표준 라이브러리 · pydantic 은 막지 않는다
#: (yaml 은 domain 만 막는다).
_FORBIDDEN: dict[str, tuple[str, ...]] = {
    #  판단·계산: 입력 → 출력만. 부서 선언은 **넘겨받는다** — 파일을 읽는 `load_constraints` ·
    #  경로 · yaml 을 들이지 않는다 (2026-09-29 BL-016 보완 — 아래 `_config_read_problems`).
    "domain": (
        "yaml",
        f"{_PKG}.config.load_constraints",
        f"{_PKG}.config.CONSTRAINTS_PATH",
        "psycopg",
        "psycopg_pool",
        "httpx",
        "langgraph",
        "urllib.request",
        "dotenv",
        "app.core.db",
        "app.core.settings",
        "app.core.clock",
        "app.master",
        f"{_PKG}.service",
        f"{_PKG}.repository",
        f"{_PKG}.readmodel",
        f"{_PKG}.adapter",
        f"{_PKG}.ports",
        f"{_PKG}.mocks",
        f"{_PKG}.features",
        *_LLM_CALLERS,
        *_HTTP,
    ),
    #  업무 순서: HTTP 입구 · 어댑터를 모른다.
    "service": (f"{_PKG}.adapter", *_HTTP),
    #  SQL: 판단 · 조립 · 순서 · 외부 호출을 부르지 않는다.
    "repository": (
        f"{_PKG}.domain",
        f"{_PKG}.service",
        f"{_PKG}.readmodel",
        f"{_PKG}.llm",
        f"{_PKG}.adapter",
        f"{_PKG}.ports",
        f"{_PKG}.mocks",
        "httpx",
        "langgraph",
        *_HTTP,
    ),
    #  조회 조립: 순서 · 포트 · LLM · 그래프를 부르지 않는다.
    "readmodel": (
        f"{_PKG}.service",
        f"{_PKG}.adapter",
        f"{_PKG}.ports",
        f"{_PKG}.mocks",
        f"{_PKG}.features",
        f"{_PKG}.llm",
        "httpx",
        "langgraph",
        "app.core.clock",
        *_HTTP,
    ),
    #  모델: 다른 매입 계층을 모른다 (`proposal` 이 부서 선언을 읽는 것은 쟁점 7).
    "schemas": (
        f"{_PKG}.domain",
        f"{_PKG}.service",
        f"{_PKG}.repository",
        f"{_PKG}.readmodel",
        f"{_PKG}.llm",
        f"{_PKG}.adapter",
        f"{_PKG}.ports",
        f"{_PKG}.mocks",
        "app.core.db",
        "psycopg",
        "langgraph",
        *_HTTP,
    ),
}

#: domain 이 들이는 매입 밖 · 계층 밖 앱 모듈의 전부. 늘리려면 이유를 적는다.
_DOMAIN_APP_ALLOWED = (
    f"{_PKG}.domain",
    f"{_PKG}.schemas",
    #  넘겨받은 선언 dict 를 **해석하는** 순수 함수와 그 예외 — 파일을 읽지 않는다
    f"{_PKG}.config.ThresholdNotDeclared",
    f"{_PKG}.config.ci_width_threshold",
    f"{_PKG}.config.threshold_missing_data_name",
    f"{_PKG}.config.threshold_not_declared_reason",
    #  LLM 판단자에게 줄 재료의 **모델**과 숫자 가리기 — 호출은 하지 않는다
    f"{_PKG}.llm.review_schemas",
    f"{_PKG}.llm.text_guard",
    "app.contracts",
)


def _layer_problems(source: str, forbidden: tuple[str, ...]) -> list[str]:
    return sorted(
        f"{line}: {shape}"
        for line, shape, names in module_refs(source)
        if any(is_under(name, bad) for name in names for bad in forbidden)
    )


@pytest.mark.parametrize("layer", sorted(_FORBIDDEN))
def test_each_layer_imports_only_what_it_may(layer):
    files = _files(layer)
    assert files, f"app/purchase_agent/{layer}/ 에 파일이 없다 — 스캐너가 빈 폴더를 본다"
    offenders = {
        str(path.relative_to(_BACKEND)): problems
        for path in files
        if (problems := _layer_problems(_read(path), _FORBIDDEN[layer]))
    }

    assert offenders == {}, offenders


def test_domain_reaches_outside_only_through_the_allowlist():
    offenders = {
        str(path.relative_to(_BACKEND)): sorted(
            name
            for name in _imported(_read(path))
            if name.startswith("app.")
            and not any(
                is_under(name, allowed) or is_under(allowed, name)
                for allowed in _DOMAIN_APP_ALLOWED
            )
        )
        for path in _files("domain")
    }

    assert {path: names for path, names in offenders.items() if names} == {}


@pytest.mark.parametrize(
    ("layer", "planted"),
    [
        ("domain", "def f():\n    from app.core import db\n"),
        ("domain", "from app.purchase_agent.features import enabled\n"),
        ("domain", "from app.purchase_agent import ports\n"),
        ("domain", "from app.purchase_agent.llm.mix import make_mix_selector\n"),
        ("domain", "from app.purchase_agent.config import load_constraints\n"),
        ("domain", "import yaml\n"),
        (
            "domain",
            "import importlib\nimportlib.import_module('app.purchase_agent.repository.quotes')\n",
        ),
        ("service", "from fastapi import HTTPException\n"),
        ("repository", "from app.purchase_agent.domain.quotes import to_price\n"),
        ("readmodel", "from app.purchase_agent import ports\n"),
        ("schemas", "from app.purchase_agent.domain import information_requests\n"),
    ],
)
def test_the_layer_scan_catches_planted_shapes(layer, planted):
    assert _layer_problems(planted, _FORBIDDEN[layer])


_CONFIG = f"{_PKG}.config"
#: domain 이 `config` 에서 들일 수 있는 이름 — 넘겨받은 선언을 해석하는 순수 함수와 그 예외뿐.
_DOMAIN_CONFIG_NAMES = frozenset(
    {
        "ThresholdNotDeclared",
        "ci_width_threshold",
        "threshold_missing_data_name",
        "threshold_not_declared_reason",
    }
)
#: 코드로 쓰면 파일을 읽는 이름 — 함수 기본값의 `c or load_constraints()` 도 여기서 걸린다.
_FILE_READS = frozenset(
    {"load_constraints", "CONSTRAINTS_PATH", "safe_load", "open", "read_text", "read_bytes"}
)


def _config_read_problems(source: str) -> list[str]:
    """부서 선언을 **스스로 읽는** 모양 — 허용한 이름 밖의 `config` 이름 · 모듈 통째 · 파일 읽기."""
    problems = []
    for line, shape, names in module_refs(source):
        for name in names:
            if not is_under(name, _CONFIG):
                continue
            #  `from …config import x` 는 모듈 이름도 같이 낸다 — 아래 이름(x)으로 본다
            if name == _CONFIG and shape == f"from {_CONFIG}":
                continue
            if name.removeprefix(f"{_CONFIG}.") not in _DOMAIN_CONFIG_NAMES:
                problems.append(f"{line}: {shape} → {name}")
    problems.extend(f"코드가 {name} 을 쓴다" for name in _code_uses(source) & _FILE_READS)
    return sorted(problems)


def test_the_domain_reads_no_declaration_file():
    """domain 은 부서 선언(`constraints.yaml`)을 **넘겨받는다** — 스스로 읽지 않는다.

    2026-09-29 BL-016 보완: 19시 판에는 domain 이 `config.load_constraints()` 를 부르는 것을
    허용했다. 그 함수는 부를 때마다 파일을 읽으므로 «외부 입출력 없는 판단 · 계산» 이 아니다.
    이제 부르는 쪽(service 노드 · 어댑터)이 읽어 dict 로 넘긴다. 실제 실행에서 읽은 자리는
    `tests/test_purchase_agent/test_adapter.py` 가 따로 잰다.
    """
    offenders = {
        str(path.relative_to(_BACKEND)): problems
        for path in _files("domain")
        if (problems := _config_read_problems(_read(path)))
    }

    assert offenders == {}, offenders


@pytest.mark.parametrize(
    "planted",
    [
        "from app.purchase_agent.config import load_constraints\n",
        "from app.purchase_agent.config import CONSTRAINTS_PATH, ci_width_threshold\n",
        "from app.purchase_agent import config\n",
        "import app.purchase_agent.config as settings\n",
        "def f(constraints=None):\n    return constraints or load_constraints()\n",
        "from pathlib import Path\n\nPath('constraints.yaml').read_text()\n",
    ],
)
def test_the_declaration_read_scan_catches_planted_shapes(planted):
    assert _config_read_problems(planted)


def test_the_declaration_read_scan_lets_the_pure_helpers_through():
    assert _config_read_problems(
        "from app.purchase_agent.config import ThresholdNotDeclared, ci_width_threshold\n"
    ) == []


def test_the_domain_does_not_load_db_http_llm_calls_or_flags_at_runtime():
    """import 목록만이 아니라 **실제로 불러와지는 모듈**로도 잰다 (새 파이썬 하나)."""
    domain = sorted(_module_name(path) for path in _files("domain"))
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
        "dotenv",
        "app.core.db",
        "app.core.clock",
        f"{_PKG}.features",
        f"{_PKG}.ports",
        f"{_PKG}.mocks",
        f"{_PKG}.llm.runtime",
        f"{_PKG}.repository",
        f"{_PKG}.readmodel",
        f"{_PKG}.service",
    ):
        assert heavy not in loaded, heavy


def test_node_functions_live_in_the_service_layer():
    """그래프가 거는 노드 여덟은 `service/nodes/` 의 함수다 — 판정 · 계산은 같은 이름의 domain."""
    from app.purchase_agent.service.graph import NODES

    assert len(NODES) == 8
    for name, node in NODES.items():
        assert node.__module__ == f"{_PKG}.service.nodes.{name}", (name, node.__module__)
        assert (_PA / "domain" / f"{name}.py").exists(), f"domain/{name}.py 가 없다"


# ---------------------------------------------------------------------------
# 3. 읽기 전용 — SQL 은 repository, 조회는 조회 연결, 쓰기 없음 (규칙 2)
# ---------------------------------------------------------------------------


def _code_uses(source: str) -> set[str]:
    """원문이 **코드로** 쓰는 이름 · 속성 (문자열 · 주석 · docstring 은 빼고)."""
    used: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
    return used


def _code_strings(source: str) -> list[str]:
    """코드가 쓰는 문자열 리터럴 (docstring 은 뺀다)."""
    tree = ast.parse(source)
    prose = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in prose
    ]


_SQL_USES = frozenset({"execute", "executemany", "cursor", "SQL", "Identifier", "Placeholder"})
_WRITE_USES = frozenset({"commit", "rollback", "executemany", "transaction", "connection"})
_WRITE_SQL = re.compile(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|MERGE)\b", re.IGNORECASE)


def test_sql_runs_only_in_the_repository():
    """🔴 SQL 문과 커서는 `repository/` 에만 있다. 연결을 **빌리는** 것은 readmodel 이다."""
    offenders = {
        str(path.relative_to(_BACKEND)): sorted(_code_uses(_read(path)) & _SQL_USES)
        for path in _files()
        if "repository" not in path.relative_to(_PA).parts
    }

    assert {path: uses for path, uses in offenders.items() if uses} == {}
    assert any(
        _code_uses(_read(path)) & _SQL_USES for path in _files("repository")
    ), "repository 에서 SQL 을 못 찾았다 — 검사가 빈 곳을 본다"


def test_the_purchase_package_never_writes():
    """🔴 규칙 2 — 쓰기 SQL · commit · rollback · 쓰기 대여 · 트랜잭션 블록이 **한 곳도** 없다."""
    offenders = {}
    for path in _files():
        source = _read(path)
        uses = sorted(_code_uses(source) & _WRITE_USES)
        writes = sorted({m.group(1).upper() for s in _code_strings(source) for m in
                         _WRITE_SQL.finditer(s)})
        if uses or writes:
            offenders[str(path.relative_to(_BACKEND))] = uses + writes

    assert offenders == {}


def test_the_write_scan_catches_planted_writes():
    planted = "def f(conn):\n    conn.cursor().execute('UPDATE t SET a = 1')\n    conn.commit()\n"
    assert _code_uses(planted) & _WRITE_USES == {"commit"}
    assert [m.group(1) for s in _code_strings(planted) for m in _WRITE_SQL.finditer(s)] == [
        "UPDATE"
    ]


def test_the_quote_readmodel_borrows_only_a_read_connection():
    source = _read(_PA / "readmodel" / "quotes.py")
    assert "read_connection(" in source
    assert "core_db" not in source  # 연결 모듈을 통째로 들이지 않는다 — 이름으로 조회 대여만


def test_ports_stay_the_external_input_boundary():
    """`ports.py` 는 제자리 입구다 (쟁점 12). 부르는 쪽은 service 뿐이고, ports 는 mock · 시세
    공급자 타입 · 출력 계약 상수만 들인다."""
    callers = _importers_of(f"{_PKG}.ports")
    assert callers and all(is_under(name, f"{_PKG}.service") for name in callers), callers
    imported = {name for name in _imported(_read(_PA / "ports.py")) if name.startswith(_PKG)}
    assert all(
        any(is_under(name, ok) or is_under(ok, name) for ok in (
            f"{_PKG}.mocks", f"{_PKG}.readmodel.quotes", f"{_PKG}.schemas.proposal"))
        for name in imported
    ), imported


# ---------------------------------------------------------------------------
# 4. 순환 · 함수 안 import · private 이름
# ---------------------------------------------------------------------------


def _top_level_edges() -> dict[str, set[str]]:
    modules = {_module_name(path): path for path in _files()}
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


def test_purchase_has_no_import_cycle():
    edges = _top_level_edges()
    assert len(edges) > 45, "매입 모듈을 거의 못 읽었다 — 스캐너가 빈 곳을 본다"

    assert _cycles(edges) == []


def test_the_cycle_finder_sees_a_planted_cycle():
    assert _cycles({"a": {"b"}, "b": {"a"}, "c": set()}) == [["a", "b"]]


#: 함수 안 import 를 두는 자리 — LLM 런타임의 SDK · 표준 HTTP 지연 로딩뿐이다 (무거운 SDK 는
#: 설계서가 허용하는 예외, 프로바이더 정리는 BL-020). 매입 계층 이동으로 생긴 것은 없다.
_LAZY_IMPORT_OWNERS = {f"{_PKG}.llm.runtime"}


def test_purchase_has_no_function_level_import():
    """🔴 순환을 피하려고 import 를 함수 안으로 숨기지 않는다."""
    offenders = []
    for path in _files():
        if _module_name(path) in _LAZY_IMPORT_OWNERS:
            continue
        for node in ast.walk(ast.parse(_read(path))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                offenders += [
                    f"{path.relative_to(_BACKEND)}:{inner.lineno}"
                    for inner in ast.walk(node)
                    if isinstance(inner, (ast.Import, ast.ImportFrom))
                ]

    assert offenders == []


def test_no_module_imports_a_private_purchase_name():
    """다른 모듈의 `_이름` 을 가져다 쓰지 않는다 — 필요하면 공개 이름으로 올린다."""
    offenders = [
        f"{path.relative_to(_BACKEND)}: from {node.module} import {alias.name}"
        for path in python_files(_APP)
        for node in ast.walk(ast.parse(_read(path)))
        if isinstance(node, ast.ImportFrom)
        and node.module
        and is_under(node.module, _PKG)
        for alias in node.names
        if alias.name.startswith("_")
    ]

    assert offenders == []
