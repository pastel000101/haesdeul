"""마스터 계층의 방향 — 2026-09-30 재구성 BL-018.

```text
화면 · 부서 HTTP  app/api/master/*.py · app/api/critic/*.py → service · readmodel · report
                  (2026-09-30 재구성 BL-019 전에는 app/master/router.py · critic/router.py)
CLI 셋            app/master/cli/*        → service · readmodel · report · registry.bootstrap
등록소 조립       registry/bootstrap.py   → 부서 adapter · adapters/finance_parts
service           업무 순서 · 트랜잭션 경계 → domain · repository · readmodel · registry · report
readmodel         조회 연결을 빌려 repository 를 부르고 결과를 조립한다
repository        받은 연결로 SQL 만 — 빌리지도 commit 하지도 않는다
domain            판정 · 계산 · 결과 모델 — DB · HTTP · LLM 호출 · 파일 · 환경 · 시계 없음
schemas           요청 · 응답 · 어휘 — 다른 계층을 모른다
```

이 파일이 잠그는 것:

1. 계층별로 들일 수 있는 것(마스터 안 · 밖). 좁은 예외 셋은 이름까지 적는다 — 이력 조회가 매입
   보고서 문장을 싣는 자리(`readmodel/history.py` → `report/purchase_report`), 재무 파트
   표면이 쥔 기본 읽기 자리(`adapters/finance_parts.py`), 보고서가 읽는 마감 칸 이름 상수
   (`report` → repository 의 대문자 이름) · CLI 가 쓰는 repository 결과 타입(대문자로 시작하는
   이름).
2. 연결 — repository 는 빌리지도 끝내지도 않는다. readmodel 은 조회 연결만 빌린다(종전 경계를 옮긴
   물류 보고서 한 곳 예외). service 에서 연결 모듈을 직접 부르는 함수는 표에 적힌 여섯뿐이다 —
   나머지 service 는 받은 `borrow` · `conn` 으로 일한다. domain · schemas · 등록소 · adapters ·
   report 는 연결 모듈을 들이지 않는다.
3. SQL 은 repository 에만 있다. `master/db.py` 는 없고, 그 헬퍼 이름(`fetch_one` · `fetch_all` ·
   `execute_returning_one`)을 다시 정의한 범용 중계 모듈도 없다.
4. domain 은 시계를 읽지 않는다(시간대 상수 `SEOUL` 하나만).
5. adapter 를 들이는 자리 — 부서 adapter 와 재무 파트 표면은 등록소 조립 하나, Critic 다리는 검증
   service 하나. 마스터 패키지에는 FastAPI 가 없다 — HTTP 입구는 `app/api/master` · `app/api/critic`
   이고, 그 입구가 들일 수 있는 것도 1 의 표로 잰다(2026-09-30 재구성 BL-019).
6. 모듈 맨 위 import 만 쓰고(함수 안 import 없음), 다른 마스터 모듈의 private 이름을 들이지 않고,
   마스터 모듈 사이에 import 순환이 없다.

★ 스캐너는 `import_scan.py` 한 벌이다(함수 안 · 문자열 동적 import 까지 센다). 검사마다 **심어 둔
  모양을 스스로 잡는지**도 잰다 — 0건을 세는 검사가 공짜로 초록이 되지 않게.
★ `critic/` · `llm/` · `cycle_llm/` 은 재구성 전부터 따로 선 하위 패키지라 1 · 2 의 계층 표에서 뺀다
  (SQL 자리 · private 이름 · 순환 검사는 받는다).
"""

from __future__ import annotations

import ast
from pathlib import Path

from import_scan import is_under, module_refs, python_files

import app

_APP = Path(app.__file__).parent
_MASTER = _APP / "master"
_PKG = "app.master"
_SEPARATE = ("critic", "llm", "cycle_llm")
_DEPARTMENTS = ("finance", "logistics", "sales", "purchase_agent", "ml")

#: 계층 → 들여도 되는 마스터 계층(자기 계층 포함). `llm.schemas` · `cycle_llm.schemas` 는 LLM 입출력
#: 타입이다. `router` 는 마스터 HTTP 입구 `app/api/master/*.py`, `critic_router` 는 Critic HTTP 입구
#: `app/api/critic/*.py` 다 — 2026-09-30 재구성 BL-019 에 마스터 폴더 밖으로 옮겨서 계층 폴더 검사와
#: 따로 잰다(`test_http_entries_import_only_what_they_may`).
_MAY_IMPORT: dict[str, frozenset[str]] = {
    "schemas": frozenset({"schemas", "llm.schemas", "cycle_llm.schemas"}),
    "domain": frozenset({"domain", "schemas", "llm.schemas"}),
    "repository": frozenset({"repository", "domain", "schemas"}),
    "readmodel": frozenset({"readmodel", "repository", "domain", "schemas"}),
    "service": frozenset(
        {
            "service", "readmodel", "repository", "domain", "schemas", "registry", "report",
            "llm", "llm.schemas", "critic", "adapters",
        }
    ),
    "registry": frozenset({"registry", "domain", "schemas", "adapters"}),
    "adapters": frozenset(
        {"adapters", "service", "readmodel", "repository", "domain", "schemas", "critic"}
    ),
    "report": frozenset({"report", "readmodel", "repository", "domain", "schemas"}),
    "cli": frozenset(
        {"cli", "service", "readmodel", "repository", "report", "registry", "domain", "schemas"}
    ),
    "router": frozenset({"service", "readmodel", "report", "domain", "schemas"}),
    "critic_router": frozenset({"critic", "service", "readmodel"}),
    "__init__": frozenset(),
}

#: 부서 패키지에서 들여도 되는 계층 — 계층마다.
_DEPT_MAY_TAKE: dict[str, frozenset[str]] = {
    "schemas": frozenset({"schemas"}),
    "domain": frozenset({"schemas"}),
    "repository": frozenset({"schemas"}),
    "readmodel": frozenset({"schemas", "readmodel"}),
    "service": frozenset({"schemas", "domain", "readmodel", "service"}),
    "registry": frozenset({"adapter", "domain", "readmodel"}),
    "adapters": frozenset({"schemas", "readmodel", "service"}),
    "report": frozenset({"schemas", "domain", "readmodel"}),
    "cli": frozenset(),
    "router": frozenset(),
    "critic_router": frozenset(),
    "__init__": frozenset(),
}

_HTTP = ("fastapi", "starlette", "app.api")
_SIDE_EFFECTS = (
    "psycopg", "httpx", "requests", "urllib", "os", "pathlib", "dotenv", "time",
    "anthropic", "openai",
    "app.core.db", "app.core.settings", "app.core.llm",
)

#: 계층 → 들이면 안 되는 바깥 모듈(앞머리 일치).
_MAY_NOT_TAKE: dict[str, tuple[str, ...]] = {
    "schemas": (*_HTTP, *_SIDE_EFFECTS),
    "domain": (*_HTTP, *_SIDE_EFFECTS),
    "repository": (*_HTTP, "app.core.db", "app.core.llm", "anthropic", "openai"),
    "readmodel": (*_HTTP, "app.core.llm", "anthropic", "openai"),
    "service": _HTTP,
    "registry": (*_HTTP, "psycopg", "app.core.db", "app.core.settings"),
    "adapters": (*_HTTP, "psycopg", "app.core.db", "app.core.settings"),
    "report": (*_HTTP, "psycopg", "app.core.db", "app.core.settings", "app.core.llm"),
    "cli": _HTTP,
    "router": ("app.api", "psycopg", "app.core.db"),
    "critic_router": ("app.api", "psycopg", "app.core.db"),
    "__init__": (),
}

#: 마스터 폴더 밖의 마스터 HTTP 입구 — 위 표의 계층 이름 → 폴더.
_HTTP_ENTRY_DIRS: dict[str, Path] = {
    "router": _APP / "api" / "master",
    "critic_router": _APP / "api" / "critic",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _rel(path: Path) -> str:
    return path.relative_to(_MASTER).as_posix()


def _layer(path: Path) -> str:
    parts = path.relative_to(_MASTER).parts
    return parts[0] if len(parts) > 1 else path.stem


def _layered_files() -> list[Path]:
    return [path for path in python_files(_MASTER) if _layer(path) not in _SEPARATE]


def _master_layer_of(name: str) -> str | None:
    """`app.master.<계층>…` → 계층. 마스터 밖이거나 패키지 뿌리면 `None`."""
    parts = name.split(".")
    if parts[:2] != ["app", "master"] or len(parts) < 3:
        return None
    if parts[2] in ("llm", "cycle_llm") and len(parts) > 3 and parts[3] == "schemas":
        return f"{parts[2]}.schemas"
    return parts[2]


def _dept_layer_of(name: str) -> str | None:
    """`app.<부서>.<계층>…` → 계층. 부서 밖이면 `None`."""
    parts = name.split(".")
    if len(parts) < 3 or parts[0] != "app" or parts[1] not in _DEPARTMENTS:
        return None
    return parts[2]


# ---------------------------------------------------------------------------
# 1. 계층별로 들일 수 있는 것
# ---------------------------------------------------------------------------

#: 이름까지 적은 좁은 예외. `(파일, 들이는 모듈)` → 들여도 되는 이름.
_NARROW: dict[tuple[str, str], frozenset[str]] = {
    # 이력 조회가 매입 보고서 Markdown 을 함께 싣는다 — `purchase_report` 는 DB 없는 문장 조립이다.
    ("readmodel/history.py", "app.master.report.purchase_report"): frozenset(
        {"render_report", "report_filename"}
    ),
    # 재무 파트 표면(등록소 Protocol 구현)이 생성 인자 기본값으로 쥔 읽기 자리 — 종전 생성 인자
    # 그대로다.
    ("adapters/finance_parts.py", "app.master.readmodel.collection_events"): frozenset(
        {"read_collection_events"}
    ),
    ("adapters/finance_parts.py", "app.master.repository.sales_reads"): frozenset(
        {"ConfirmedSale", "read_confirmed_sales"}
    ),
}

#: (계층, 마스터 계층) → 그 계층에서 들여도 되는 이름 모양. 함수가 아니라 상수 · 타입만 — 또는
#: `_NARROW` 에 이름까지 적힌 것만(`NARROW`).
_NAME_SHAPE_ONLY: dict[tuple[str, str], str] = {
    ("report", "repository"): "UPPER",   # 마감 칸 이름 상수 — 주인은 `repository/ledger.py`
    ("cli", "repository"): "CapWords",  # repository 가 돌려주는 결과 타입
    ("adapters", "readmodel"): "NARROW",
    ("adapters", "repository"): "NARROW",
}


def _shape_ok(shape: str, name: str) -> bool:
    if shape == "UPPER":
        return name.isupper()
    if shape == "CapWords":
        return name[:1].isupper() and not name.isupper()
    return False


def _from_imports(tree: ast.AST) -> list[tuple[int, str, str, set[str]]]:
    """`(줄, 들이는 모듈, 마스터 계층, 들이는 이름)`.

    `from app.master import x` 는 `x` 를 계층으로 본다.
    """
    out: list[tuple[int, str, str, set[str]]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ImportFrom) and node.module and not node.level):
            continue
        if node.module == _PKG:
            out += [(node.lineno, f"{_PKG}.{a.name}", a.name, set()) for a in node.names]
            continue
        target = _master_layer_of(node.module)
        if target is not None:
            out.append((node.lineno, node.module, target, {a.name for a in node.names}))
    return out


def _layer_violations(rel: str, layer: str, source: str) -> list[str]:
    bad: list[str] = []
    for line, module, target, names in _from_imports(ast.parse(source)):
        narrow = _NARROW.get((rel, module))
        shape = _NAME_SHAPE_ONLY.get((layer, target))
        if narrow is not None:
            if names - narrow:
                bad.append(f"{line}: from {module} import {sorted(names - narrow)} (좁은 예외 밖)")
        elif target not in _MAY_IMPORT[layer]:
            bad.append(f"{line}: from {module} → {target}")
        elif shape == "NARROW":
            bad.append(f"{line}: from {module} (좁은 예외 밖)")
        elif shape is not None and (wrong := sorted(n for n in names if not _shape_ok(shape, n))):
            bad.append(f"{line}: from {module} import {wrong} ({shape} 만)")
    for line, shape_text, names in module_refs(source):
        for name in names:
            target = _master_layer_of(name)
            if (
                target is not None
                and not shape_text.startswith("from ")
                and target not in _MAY_IMPORT[layer]
            ):
                bad.append(f"{line}: {shape_text} → {target}")
                break
            dept = _dept_layer_of(name)
            if dept is not None and dept not in _DEPT_MAY_TAKE[layer]:
                bad.append(f"{line}: {shape_text} → 부서 {dept}")
                break
            if any(is_under(name, banned) for banned in _MAY_NOT_TAKE[layer]):
                bad.append(f"{line}: {shape_text}")
                break
    return bad


def test_layers_import_only_what_they_may() -> None:
    found = {
        _rel(path): hits
        for path in _layered_files()
        if (hits := _layer_violations(_rel(path), _layer(path), _read(path)))
    }
    assert found == {}, found


def test_scanner_reads_the_layer_folders() -> None:
    """★ 0 개를 읽으면 위 검사가 공짜 초록이 된다. 무엇을 읽었는지를 먼저 잰다."""
    layers = {_layer(path) for path in _layered_files()}
    expected = set(_MAY_IMPORT) - set(_HTTP_ENTRY_DIRS)
    assert expected <= layers, sorted(expected - layers)
    assert len(_layered_files()) > 150, len(_layered_files())


def test_layer_check_catches_planted_imports() -> None:
    def bad(rel: str, source: str) -> list[str]:
        return _layer_violations(rel, rel.split("/")[0] if "/" in rel else rel[:-3], source)

    assert bad("schemas/x.py", "from app.master.domain.plan import ExecutionPlan\n")
    assert bad("domain/x.py", "from app.master.repository.runs import insert_run\n")
    assert bad("domain/x.py", "from app.master.readmodel.runs import list_runs\n")
    assert bad("domain/x.py", "from app.master import service\n")
    assert bad("domain/x.py", "importlib.import_module('app.master.repository.runs')\n")
    assert bad("domain/x.py", "import os\n")
    assert bad("domain/x.py", "from app.core.settings import get_db_schema\n")
    assert bad("domain/x.py", "from app.finance.service.expenses import settle\n")
    assert bad("repository/x.py", "from app.core import db as core_db\n")
    assert bad("repository/x.py", "from app.master.service.run_history import save_run\n")
    assert bad("readmodel/x.py", "from app.master.service.decision import save_decision\n")
    assert bad("readmodel/x.py", "from app.master.report.walk_report import walk_report\n")
    assert bad("readmodel/history.py", "from app.master.report.walk_report import walk_report\n")
    assert bad("service/x.py", "from fastapi import HTTPException\n")
    assert bad("service/x.py", "from app.finance.adapter import finance_port\n")
    assert bad("service/x.py", "from app.api.master.flows import router\n")
    assert bad("report/x.py", "from app.master.repository.ledger import persist_purchases\n")
    assert bad("report/x.py", "from app.core import db as core_db\n")
    assert bad("cli/x.py", "from app.master.repository.sim_runs import create_sim_run\n")
    assert bad(
        "adapters/finance_parts.py", "from app.master.repository.ledger import persist_purchases\n"
    )
    assert bad("registry/x.py", "from app.master.service.day_open import open_day\n")
    assert not bad(
        "readmodel/history.py", "from app.master.report.purchase_report import render_report\n"
    )
    assert not bad("report/x.py", "from app.master.repository.ledger import NET_CASH\n")
    assert not bad("cli/x.py", "from app.master.repository.sim_run_open import BaselineLineage\n")
    assert not bad("schemas/x.py", "from app.master.llm.schemas import Intent\n")
    assert not bad("domain/x.py", "from app.core.clock import SEOUL\n")


# ---------------------------------------------------------------------------
# 2. 연결 — 누가 빌리고 누가 끝내나
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


def _takes_connection_module(source: str) -> bool:
    return any(
        is_under(name, "app.core.db")
        for _line, _shape, names in module_refs(source)
        for name in names
    )


def _function_calls(source: str) -> dict[str, set[str]]:
    """맨 위 함수(클래스 메서드 포함)마다 연결 모듈 부름. 이름은 `함수` 또는 `클래스.메서드`."""
    out: dict[str, set[str]] = {}
    for node in ast.parse(source).body:
        pairs: list[tuple[str, ast.AST]] = []
        if isinstance(node, ast.FunctionDef):
            pairs.append((node.name, node))
        elif isinstance(node, ast.ClassDef):
            pairs += [
                (f"{node.name}.{sub.name}", sub)
                for sub in node.body
                if isinstance(sub, ast.FunctionDef)
            ]
        for name, fn in pairs:
            calls = {
                call
                for call in _connection_calls(ast.unparse(fn))
                if call.startswith("core_db.")
            }
            if calls:
                out[name] = calls
    return out


def test_repository_neither_borrows_nor_ends_transactions() -> None:
    found = {
        _rel(path): calls
        for path in python_files(_MASTER / "repository")
        if (calls := _connection_calls(_read(path)))
        or (_takes_connection_module(_read(path)) and (calls := {"import app.core.db"}))
    }
    assert found == {}, found


#: readmodel 에서 조회 연결 말고 쓰기 연결 종류를 쓰는 함수 — 모두 종전 경계를 옮긴 자리다.
#: 채팅 물류 보고서는 종전(`master/report.py`)부터 `connection()` + `transaction` 블록 하나로 물류
#: readmodel 일곱을 읽었다. 개장 조회 둘 · 수금 사건 읽기는 넘겨받은 `borrow` 가 없으면 종전처럼
#: `core_db.connection` 으로 빌린다(개장 관문 · 수금 경계가 자기 연결을 넘기는 것과 같은 종류).
_READMODEL_WRITE_KIND: dict[tuple[str, str], frozenset[str]] = {
    ("readmodel/logistics_report.py", "read_logistics_report_facts"): frozenset(
        {"core_db.connection", "core_db.transaction"}
    ),
    ("readmodel/day_openings.py", "read_day_opening"): frozenset({"core_db.connection"}),
    ("readmodel/day_openings.py", "opened_days_after"): frozenset({"core_db.connection"}),
    ("readmodel/collection_events.py", "read_collection_events"): frozenset({"core_db.connection"}),
}
_BORROWERS = frozenset({"connection", "read_connection", "transaction"})


def _function_refs(source: str) -> dict[str, set[str]]:
    """맨 위 함수마다 연결 모듈 대여 이름을 **가리키는** 자리 — 부름뿐 아니라
    `core_db.connection if borrow is None else borrow` 처럼 넘기는 것도 센다(타입 이름은 뺀다)."""
    out: dict[str, set[str]] = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef):
            refs = {
                f"core_db.{sub.attr}"
                for sub in ast.walk(node)
                if isinstance(sub, ast.Attribute)
                and isinstance(sub.value, ast.Name)
                and sub.value.id == "core_db"
                and sub.attr in _BORROWERS
            }
            if refs:
                out[node.name] = refs
    return out


def test_readmodel_borrows_only_read_connections() -> None:
    """★ 조회는 `read_connection` 을 빌린다(종전 헬퍼와 같은 종류). 쓰기 연결 종류는 표에 적힌 종전
    경계 넷뿐이고, commit · rollback 을 직접 부르는 곳은 없다."""
    found: dict[str, object] = {}
    for path in python_files(_MASTER / "readmodel"):
        source = _read(path)
        if {".commit", ".rollback"} & _connection_calls(source):
            found[_rel(path)] = _connection_calls(source)
            continue
        for name, refs in _function_refs(source).items():
            extra = refs - {"core_db.read_connection"}
            extra -= _READMODEL_WRITE_KIND.get((_rel(path), name), frozenset())
            if extra:
                found[f"{_rel(path)}::{name}"] = extra
    assert found == {}, found


def test_readmodel_ref_check_catches_planted_borrows() -> None:
    planted = (
        "def f(borrow=None):\n"
        "    open_connection = core_db.connection if borrow is None else borrow\n"
        "    with open_connection() as c:\n"
        "        pass\n"
        "def g(borrow: core_db.Borrow | None = None):\n"
        "    with core_db.read_connection() as c:\n"
        "        pass\n"
    )
    assert _function_refs(planted) == {
        "f": {"core_db.connection"},
        "g": {"core_db.read_connection"},
    }


#: service 에서 연결 모듈을 **직접** 부르는 함수 — 마스터가 연결을 넘기지 않는 입구뿐이다. 나머지
#: service(하루 단계 · 승인 전이 · 출고 흐름 · 실매입 기록 …)는 받은 `borrow` 로 빌리고 스스로
#: commit · rollback 한다.
_SERVICE_BORROWS: dict[tuple[str, str], frozenset[str]] = {
    # 실행 이력 1건 = 연결 하나 · 트랜잭션 하나(종전 `execute_returning_one`).
    ("service/run_history.py", "save_run"): frozenset(
        {"core_db.connection", "core_db.transaction"}
    ),
    ("service/cycle_persistence.py", "save_run"): frozenset(
        {"core_db.connection", "core_db.transaction"}
    ),
    # 결정 1건 = 연결 하나 · 트랜잭션 하나(종전 `execute_returning_one`).
    ("service/decision.py", "save_decision"): frozenset(
        {"core_db.connection", "core_db.transaction"}
    ),
    # ⚠️ 종전 `fetch_one` 헬퍼로 돌던 UPDATE … RETURNING — 조회 연결(autocommit)이 한 문장을
    #   그대로 확정한다. 경계를 바꾸지 않았다(설계서 §변경 제안).
    ("service/decision.py", "link_follow_up"): frozenset({"core_db.read_connection"}),
    # 채팅의 재무 쓰기 — 연결 하나를 빌려 재무 service 에 넘긴다(종전 그대로).
    ("service/ask_domain_actions.py", "_domain_write"): frozenset({"core_db.connection"}),
    # 걸은 시각 도장 — 연결 하나를 빌려 쓰고 스스로 commit · rollback(종전 그대로).
    ("service/walk_provenance.py", "record_walked_now"): frozenset({"core_db.connection"}),
}


def test_service_borrows_directly_only_where_listed() -> None:
    found = {
        (_rel(path), name): frozenset(calls)
        for path in python_files(_MASTER / "service")
        for name, calls in _function_calls(_read(path)).items()
    }
    assert found == _SERVICE_BORROWS, sorted(set(found.items()) ^ set(_SERVICE_BORROWS.items()))


def test_pure_layers_never_touch_the_connection_module() -> None:
    """domain · schemas · 등록소 · adapters · report 는 연결 모듈을 들이지도, 끝내지도 않는다."""
    found = {
        _rel(path): (_connection_calls(_read(path)), _takes_connection_module(_read(path)))
        for layer in ("domain", "schemas", "registry", "adapters", "report")
        for path in python_files(_MASTER / layer)
        if _connection_calls(_read(path)) or _takes_connection_module(_read(path))
    }
    assert found == {}, found


def test_cli_opens_the_pool_and_borrows_only_where_listed() -> None:
    found = {
        (_rel(path), name): frozenset(calls)
        for path in python_files(_MASTER / "cli")
        for name, calls in _function_calls(_read(path)).items()
    }
    assert found == {
        ("cli/backfill_runner.py", "main"): frozenset({"core_db.pool_lifespan"}),
        ("cli/backtest_runner.py", "main"): frozenset({"core_db.pool_lifespan"}),
        # 실행 열기 한 번 = 연결 하나 — 여는 순서 · commit 은 `service/sim_run.open_sim_run`.
        ("cli/sim_run_runner.py", "main"): frozenset(
            {"core_db.pool_lifespan", "core_db.connection"}
        ),
    }, found


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
    assert not _takes_connection_module("from app.core.settings import get_db_schema\n")
    assert _function_calls(
        "class A:\n    def f(self):\n"
        "        with core_db.read_connection() as c:\n            pass\n"
    ) == {"A.f": {"core_db.read_connection"}}


# ---------------------------------------------------------------------------
# 3. SQL 의 자리 · 없어진 입구
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
        for path in python_files(_MASTER)
        if _layer(path) != "repository" and (hits := _sql_marks(_read(path)))
    }
    assert found == {}, found


def test_sql_check_catches_planted_sql() -> None:
    assert _sql_marks("from psycopg import sql\n")
    assert _sql_marks("with conn.cursor() as c:\n    c.execute('SELECT 1')\n")
    assert not _sql_marks("import psycopg\nerr = psycopg.errors.UniqueViolation\n")


_OLD_HELPERS = frozenset({"fetch_one", "fetch_all", "execute_returning_one"})


def _helper_definitions(source: str) -> list[str]:
    """옛 입구 헬퍼 이름의 정의 · 모듈 수준 대입(별칭) · `__all__` 로 남의 이름 내보내기(재수출)."""
    tree = ast.parse(source)
    imported = {
        alias.asname or alias.name.split(".")[0]
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    bad: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _OLD_HELPERS:
            bad.append(f"{node.lineno}: def {node.name}")
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in _OLD_HELPERS | {"get_db_schema"}:
                    bad.append(f"{node.lineno}: {target.id} =")
                elif isinstance(target, ast.Name) and target.id == "__all__":
                    listed = {
                        elt.value
                        for elt in getattr(node.value, "elts", [])
                        if isinstance(elt, ast.Constant)
                    }
                    if listed & imported:
                        bad.append(f"{node.lineno}: __all__ 재수출 {sorted(listed & imported)}")
    return bad


def test_the_master_db_entry_is_gone_and_not_renamed() -> None:
    """🔴 `master/db.py` 를 지웠다 — 이름만 바꾼 범용 중계 모듈로 되살리지 않는다."""
    assert not (_MASTER / "db.py").exists()
    found = {
        _rel(path): hits
        for path in _layered_files()
        if (hits := _helper_definitions(_read(path)))
    }
    assert found == {}, found
    assert _importers(lambda name: is_under(name, f"{_PKG}.db")) == []


def test_helper_check_catches_planted_relays() -> None:
    assert _helper_definitions("def fetch_all(query, params=None):\n    pass\n")
    assert _helper_definitions(
        "from app.core.settings import get_db_schema as g\nget_db_schema = g\n"
    )
    assert _helper_definitions(
        "from app.core.settings import get_db_schema\n__all__ = ['get_db_schema']\n"
    )
    assert not _helper_definitions("def state_of():\n    pass\n__all__ = ['state_of']\n")
    assert not _helper_definitions("def select_runs(conn, *, schema):\n    pass\n")


# ---------------------------------------------------------------------------
# 4. domain 은 시계를 읽지 않는다
# ---------------------------------------------------------------------------


def _clock_reads(source: str) -> list[str]:
    bad: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (
                node.func.attr in {"now", "today", "utcnow"}
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"datetime", "date"}
            ):
                bad.append(f"{node.lineno}: {ast.unparse(node.func)}()")
        elif isinstance(node, ast.ImportFrom) and node.module == "app.core.clock":
            names = {alias.name for alias in node.names}
            if names - {"SEOUL"}:
                bad.append(f"{node.lineno}: from app.core.clock import {sorted(names)}")
        elif isinstance(node, ast.ImportFrom) and node.module == "app.core":
            if any(alias.name == "clock" for alias in node.names):
                bad.append(f"{node.lineno}: from app.core import clock")
        elif isinstance(node, ast.Import) and any(a.name == "app.core.clock" for a in node.names):
            bad.append(f"{node.lineno}: import app.core.clock")
    return bad


def test_domain_reads_no_clock_only_the_timezone_constant() -> None:
    found = {
        _rel(path): hits
        for path in python_files(_MASTER / "domain")
        if (hits := _clock_reads(_read(path)))
    }
    assert found == {}, found


def test_clock_check_catches_planted_reads() -> None:
    assert _clock_reads("x = datetime.now()\n")
    assert _clock_reads("x = date.today()\n")
    assert _clock_reads("from app.core import clock\n")
    assert _clock_reads("from app.core.clock import SEOUL, seoul_now\n")
    assert not _clock_reads("from app.core.clock import SEOUL\n")


# ---------------------------------------------------------------------------
# 5. adapter 와 HTTP 를 들이는 자리
# ---------------------------------------------------------------------------


def _importers(predicate) -> list[str]:
    return sorted(
        path.relative_to(_APP).as_posix()
        for path in python_files(_APP)
        if any(
            predicate(name) for _line, _shape, names in module_refs(_read(path)) for name in names
        )
    )


def test_adapters_are_imported_where_listed() -> None:
    """★ 부서 adapter · 재무 파트 표면은 등록소 조립이 한 번 들인다. Critic 다리는 검증
    service 가 부른다."""
    master_side = [
        rel
        for rel in _importers(
            lambda name: any(is_under(name, f"app.{dept}.adapter") for dept in _DEPARTMENTS)
        )
        if rel.startswith("master/")
    ]
    assert master_side == ["master/registry/bootstrap.py"], master_side
    assert _importers(lambda name: is_under(name, f"{_PKG}.adapters.finance_parts")) == [
        "master/registry/bootstrap.py"
    ]
    assert _importers(lambda name: is_under(name, f"{_PKG}.adapters.critic_bridge")) == [
        "master/service/verifier.py"
    ]


def _takes_fastapi(source: str) -> bool:
    return any(
        is_under(name, prefix)
        for _line, _shape, names in module_refs(source)
        for name in names
        for prefix in ("fastapi", "starlette")
    )


def test_master_package_has_no_fastapi() -> None:
    """HTTP 상태 코드 · `Depends` 는 HTTP 입구에만 있다 — 마스터 패키지에는 없다.

    ★ 2026-09-30 재구성 BL-019: 종전 두 라우터(`router.py` · `critic/router.py`)를
      `app/api/master/` · `app/api/critic/` 로 옮겼다. 스캐너가 FastAPI 를 실제로 잡는지는
      옮긴 입구 파일로 잰다 — 0 건을 세는 검사가 공짜로 초록이 되지 않게.
    """
    found = sorted(_rel(path) for path in python_files(_MASTER) if _takes_fastapi(_read(path)))
    assert found == [], found
    entries = [
        path
        for root in _HTTP_ENTRY_DIRS.values()
        for path in python_files(root)
        if path.name != "__init__.py"
    ]
    assert len(entries) == 7, [path.name for path in entries]
    assert all(_takes_fastapi(_read(path)) for path in entries)


def test_http_entries_import_only_what_they_may() -> None:
    """마스터 · Critic HTTP 입구는 service · readmodel · report · domain · schemas(Critic 은 그
    하위 패키지 · 실행이력 service · readmodel)만 들인다 — 연결 모듈 · 부서 · 다른 화면은 없다.
    """
    found = {}
    for layer, root in _HTTP_ENTRY_DIRS.items():
        for path in python_files(root):
            rel = path.relative_to(_APP).as_posix()
            if hits := _layer_violations(rel, layer, _read(path)):
                found[rel] = hits
    assert found == {}, found


def test_http_entry_check_catches_planted_imports() -> None:
    def bad(layer: str, source: str) -> list[str]:
        return _layer_violations("api/x.py", layer, source)

    assert bad("router", "from app.master.repository.runs import insert_run\n")
    assert bad("router", "from app.core import db as core_db\n")
    assert bad("router", "from app.finance.service import expenses\n")
    assert bad("critic_router", "from app.master.domain.plan import ExecutionPlan\n")
    assert not bad("critic_router", "from app.master.critic.service import run_critic_sales\n")
    assert not bad("router", "from app.master.service.sales import run_sales\n")


# ---------------------------------------------------------------------------
# 6. import 모양 — 맨 위 import · private 이름 · 순환
# ---------------------------------------------------------------------------


def _nested_imports(source: str) -> list[str]:
    tree = ast.parse(source)
    top = {id(node) for node in tree.body}
    return [
        f"{node.lineno}: {ast.unparse(node)}"
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom)) and id(node) not in top
    ]


def test_layer_modules_import_only_at_the_top() -> None:
    """🔴 순환을 피하려고 함수 안에서 들이지 않는다 — 순환이 생기면 계층이 틀린 것이다."""
    found = {
        _rel(path): hits for path in _layered_files() if (hits := _nested_imports(_read(path)))
    }
    assert found == {}, found


def _private_imports(source: str) -> list[str]:
    return [
        f"{node.lineno}: from {node.module} import {alias.name}"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module and is_under(node.module, _PKG)
        for alias in node.names
        if alias.name.startswith("_")
    ]


def test_no_private_names_cross_master_modules() -> None:
    found = {
        _rel(path): hits
        for path in python_files(_MASTER)
        if (hits := _private_imports(_read(path)))
    }
    assert found == {}, found


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(_APP.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _top_level_graph(files: list[Path]) -> dict[str, set[str]]:
    names = {_module_name(path): path for path in files}
    graph: dict[str, set[str]] = {name: set() for name in names}
    for name, path in names.items():
        for node in ast.parse(_read(path)).body:
            targets: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                targets = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
            elif isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            graph[name] |= {t for t in targets if t in names and t != name}
    return graph


def _cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """강한 연결 요소 중 둘 이상 묶인 것(Tarjan)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    out: list[list[str]] = []
    counter = [0]

    def visit(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in graph[v]:
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            group: list[str] = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                group.append(w)
                if w == v:
                    break
            if len(group) > 1:
                out.append(sorted(group))

    for v in graph:
        if v not in index:
            visit(v)
    return out


def test_no_import_cycles_inside_master() -> None:
    assert _cycles(_top_level_graph(python_files(_MASTER))) == []


def test_import_shape_checks_catch_planted_forms() -> None:
    assert _nested_imports("def f():\n    from app.master.domain import plan\n")
    assert not _nested_imports("from app.master.domain import plan\n")
    assert _private_imports("from app.master.domain.plan import _hidden\n")
    assert not _private_imports("from app.logistics.service.ledger import _record_disposal_move\n")
    assert _cycles({"a": {"b"}, "b": {"a"}, "c": set()}) == [["a", "b"]]
    assert _cycles({"a": {"b"}, "b": set()}) == []
