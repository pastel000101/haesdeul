"""화면이 보는 실행 ID 의 기준은 `app/core/settings.py` 의 `SHOWN_SIM_RUN_ID` 한 자리다.

화면은 `GET /api/console/shown-run` 으로 그 값을 받아 요청에 싣고, HTTP 입구는 요청이 비워
보낸 실행 ID 만 그 값으로 채운다. presenter · service 는 넘겨받은 값을 쓴다.

실 DB 에 닿지 않는다. 서비스 함수와 커넥션을 대역으로 바꿔 «무엇을 넘기는가» 만 본다.

```text
① app/api 아래 파이썬 소스에 BURN_IN_SIM_RUN_ID 이름이 한 번도 안 쓰인다 (AST)
② 재무 · 판매 · 물류 presenter 가 넘겨받은 실행 ID 를 서비스에 그대로 넘긴다
③ 대시보드가 넘겨받은 실행 ID 를 여섯 갈래 · 실매입 합계 · 매입 build 에 똑같이 넘긴다
④ 화면 탭 입구: 쿼리 없음 → 그때의 기준값 · 쿼리 있음 → 준 값. 공유 입구는 기준값을 준다
⑤ 프론트 기준일 코드값 == SHOWN_AS_OF
⑥ 두 값은 글자 그대로 적은 상수다 — 환경변수 등 두 번째 주인이 없다
⑦ 기준값을 읽는 자리는 설정 모듈 하나, 채우는 함수를 부르는 자리는 HTTP 입구뿐이다
⑧ 프론트 · 빌드 설정에 실행 ID 를 따로 정하는 자리(상수 · 빌드 인자 · 브라우저 저장)가 없다
```
"""

from __future__ import annotations

import ast
import re
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.console import routes as console_routes
from app.api.dashboard import presenter as dashboard_presenter
from app.api.dashboard import routes as dashboard_routes
from app.api.finance import presenter as finance_presenter
from app.api.finance import routes as finance_routes
from app.api.logistics import presenter as logistics_presenter
from app.api.logistics import routes as logistics_routes
from app.api.purchase import routes as purchase_routes
from app.api.sales import presenter as sales_presenter
from app.api.sales import routes as sales_routes
from app.core import settings
from app.core.settings import SHOWN_AS_OF
from app.logistics.readmodel import console as console_readmodel

_BACKEND = Path(__file__).resolve().parents[2]
_REPO = _BACKEND.parent
_APP_DIR = _BACKEND / "app"
_API_DIR = _APP_DIR / "api"
_DEMO_AS_OF = _REPO / "frontend" / "src" / "lib" / "demo_as_of.ts"
_BURN_IN = "BURN_IN_SIM_RUN_ID"

AS_OF = date(2026, 1, 26)
#: presenter 에 넘기는 실행 ID. 기준값과 다른 값이라, 넘겨받은 값 대신 기준값을 읽으면 잡힌다.
RUN = "SIM-TEST-PASSED"
#: 기준값 대역. 입구가 import 시점의 사본을 들고 있으면 이 값을 못 본다.
SUBSTITUTE = "SIM-TEST-SUBSTITUTE"


class _멈춤(Exception):
    """넘긴 값을 잡은 뒤 계산을 더 진행하지 않으려고 던진다."""


# ── ① 스캔 잠금 ─────────────────────────────────────────────────────────


#: 마스터 · Critic 의 HTTP 입구 — 화면이 아니다. 하루 단계 입구는 번인 축을 거절하려고
#: 그 이름을 읽는다(`api/master/days.py::_walk_axis`) — 허용하는 자리는 그 하나다.
_MASTER_HTTP = (_API_DIR / "master", _API_DIR / "critic")
_BURN_IN_GUARD = (_API_DIR / "master" / "days.py", "_walk_axis")


def _burn_in_hits(tree: ast.AST) -> list[ast.AST]:
    return [
        node
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == _BURN_IN)
        or (isinstance(node, ast.Attribute) and node.attr == _BURN_IN)
        or (isinstance(node, ast.ImportFrom) and any(a.name == _BURN_IN for a in node.names))
    ]


def test_화면_API_소스에_번인_상수_이름이_없다():
    """`import` · 이름 · 속성 어디로도 번인 상수를 쓰지 않는다. 문자열은 안 본다."""
    files = sorted(p for p in _API_DIR.rglob("*.py") if "__pycache__" not in p.parts)
    assert files, f"스캔한 파일이 0개다 — 경로가 틀렸다: {_API_DIR}"

    found: list[str] = []
    master_http: list[tuple[str, ast.AST]] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in _burn_in_hits(tree):
            if any(path.is_relative_to(root) for root in _MASTER_HTTP):
                master_http.append((str(path), node))
            else:
                found.append(f"{path.relative_to(_BACKEND)}:{node.lineno}")
    assert not found, f"화면 API 가 번인 상수를 쓴다: {found}"

    #  마스터 HTTP 입구에서는 `_walk_axis`(번인 축 거절)와 그 import 한 줄뿐이다.
    guard_path, guard_name = _BURN_IN_GUARD
    guard_tree = ast.parse(guard_path.read_text(encoding="utf-8"))
    guard = next(
        node
        for node in guard_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == guard_name
    )
    outside = [
        f"{Path(path).relative_to(_BACKEND)}:{node.lineno}"
        for path, node in master_http
        if Path(path) != guard_path
        or not (
            isinstance(node, ast.ImportFrom)
            or guard.lineno <= node.lineno <= (guard.end_lineno or guard.lineno)
        )
    ]
    assert master_http, "하루 단계 입구의 번인 거절을 못 찾았다 — 스캐너가 빈 곳을 본다"
    assert not outside, f"마스터 HTTP 입구가 번인 축 거절 밖에서 번인 상수를 쓴다: {outside}"


# ── ② 재무 · 판매 · 물류 ────────────────────────────────────────────────


def test_재무_build_가_넘겨받은_실행을_넘긴다(monkeypatch):
    잡은: list[tuple[str, str]] = []

    def 대시보드(*, sim_run_id: str, as_of: date) -> Any:
        잡은.append(("dashboard", sim_run_id))
        return object()

    def 현금흐름(*, sim_run_id: str, as_of: date, days: int) -> Any:
        잡은.append(("cashflow", sim_run_id))
        raise _멈춤

    monkeypatch.setattr(finance_presenter, "get_finance_dashboard", 대시보드)
    monkeypatch.setattr(finance_presenter, "get_finance_cashflow", 현금흐름)
    with pytest.raises(_멈춤):
        finance_presenter.build(AS_OF, "base", sim_run_id=RUN)
    assert 잡은 == [("dashboard", RUN), ("cashflow", RUN)]


def test_판매_build_가_넘겨받은_실행을_넘긴다(monkeypatch):
    잡은: list[str] = []

    def 대시보드(*, sim_run_id: str, as_of: date) -> Any:
        잡은.append(sim_run_id)
        raise _멈춤

    monkeypatch.setattr(sales_presenter, "get_sales_dashboard", 대시보드)
    with pytest.raises(_멈춤):
        sales_presenter.build(AS_OF, sim_run_id=RUN)
    assert 잡은 == [RUN]


@contextmanager
def _가짜_커넥션():
    """공통 풀에서 빌린 연결의 대역 — 화면은 한 판을 한 트랜잭션으로 읽는다(commit 은 빈 일)."""
    yield SimpleNamespace(commit=lambda: None, rollback=lambda: None)


def _물류_대역(monkeypatch) -> list[tuple[str, str]]:
    잡은: list[tuple[str, str]] = []

    def 기록(name: str, result: Any):
        def 대역(*args: Any, sim_run_id: str, **kwargs: Any) -> Any:
            잡은.append((name, sim_run_id))
            return result

        return 대역

    열림 = SimpleNamespace(has_snapshot=True, first_as_of=AS_OF, last_as_of=AS_OF)
    monkeypatch.setattr(console_readmodel.core_db, "connection", _가짜_커넥션)
    monkeypatch.setattr(console_readmodel, "runtime_coverage_at", 기록("coverage", 열림))
    monkeypatch.setattr(console_readmodel, "onhand_total_by_day", 기록("onhand", {}))
    monkeypatch.setattr(console_readmodel, "snapshot_days_between", 기록("days", set()))
    #  Runtime 읽기는 한 판에 한 번이고 두 콘솔이 나눠 쓴다.
    monkeypatch.setattr(console_readmodel, "load_console_runtime", 기록("runtime", None))
    #  그날 예약(Historical)도 한 판에 한 번 — 재고·출고 콘솔이 나눠 쓴다.
    monkeypatch.setattr(console_readmodel, "reservation_state_at", 기록("reservations", ()))
    monkeypatch.setattr(
        console_readmodel, "get_inbound_console", 기록("inbound", SimpleNamespace(in_transit=[]))
    )
    monkeypatch.setattr(console_readmodel, "get_inventory_console", 기록("inventory", None))
    monkeypatch.setattr(console_readmodel, "get_outbound_console", 기록("outbound", None))
    #  문제 장부 두 조회도 같은 실행에 선다. 창고 배치(`warehouse`)는 화면에서 빠져 부르지
    #  않는다.
    monkeypatch.setattr(console_readmodel, "live_exceptions_at", 기록("live_exceptions", None))
    monkeypatch.setattr(
        console_readmodel, "resolved_exceptions_on", 기록("resolved_exceptions", ())
    )
    return 잡은


def test_물류_build_가_넘겨받은_실행을_넘기고_출처에_적는다(monkeypatch):
    잡은 = _물류_대역(monkeypatch)
    #  대역 콘솔이 빈 값이라 판 조립은 실패한다. 넘긴 실행 ID 와 출처 글만 본다.
    result = logistics_presenter.build_result(AS_OF, "summary", sim_run_id=RUN)
    names = [name for name, _ in 잡은]
    assert names == [
        "coverage",
        "runtime",
        "reservations",
        "inventory",
        "inbound",
        "outbound",
        "live_exceptions",
        "resolved_exceptions",
    ]
    assert {run for _, run in 잡은} == {RUN}
    assert f"보고 있는 실행: {RUN} · 기준일: {AS_OF}" in (result.tab.source.note or "")


def test_물류_재고그래프가_넘겨받은_실행을_넘긴다(monkeypatch):
    잡은 = _물류_대역(monkeypatch)
    logistics_presenter.dashboard_stock(10, 5, AS_OF, sim_run_id=RUN)
    names = [name for name, _ in 잡은]
    assert names == ["coverage", "onhand", "days", "runtime", "inbound"]
    assert {run for _, run in 잡은} == {RUN}


# ── ③ 대시보드 ──────────────────────────────────────────────────────────


def test_대시보드가_모든_갈래에_같은_실행을_넘긴다(monkeypatch):
    잡은: dict[str, Any] = {}

    def 기록(name: str):
        def 대역(*args: Any, **kwargs: Any) -> Any:
            잡은[name] = kwargs.get("sim_run_id")
            return SimpleNamespace()

        return 대역

    def 매입(as_of: date, *args: Any, **kwargs: Any) -> Any:
        잡은["purchase"] = {"args": args, "kwargs": kwargs}
        raise _멈춤

    def 실매입(**kwargs: Any) -> dict:
        잡은["records"] = kwargs.get("sim_run_id")
        return {}

    monkeypatch.setattr(
        dashboard_presenter.forecast_presenter, "build", lambda *a, **k: SimpleNamespace()
    )
    #  대시보드는 여덟 갈래를 함께 띄운다. 매입만 대역이면 나머지 갈래가 실 DB 쪽에서 막힌
    #  채 돈다.
    for 갈래, 부서, 이름 in (
        (dashboard_presenter.finance_presenter, "finance", "build"),
        (dashboard_presenter.finance_presenter, "finance", "dashboard_cash"),
        (dashboard_presenter.logistics_presenter, "logistics", "build"),
        (dashboard_presenter.logistics_presenter, "logistics", "dashboard_stock"),
        (dashboard_presenter.sales_presenter, "sales", "build"),
    ):
        monkeypatch.setattr(갈래, 이름, 기록(f"{부서}.{이름}"))
    monkeypatch.setattr(dashboard_presenter, "recorded_totals_by_plan", 실매입)
    monkeypatch.setattr(dashboard_presenter.purchase_presenter, "build", 매입)
    with pytest.raises(_멈춤):
        dashboard_presenter.build(AS_OF, sim_run_id=RUN)
    #  `window_days=0` — 대시보드는 도착일을 안 읽는다. 실행 ID 가 빠지는 것도, 창이 조용히
    #  넓어지는 것도 사고다.
    assert 잡은.pop("purchase") == {"args": (), "kwargs": {"sim_run_id": RUN, "window_days": 0}}
    assert 잡은 == {
        "finance.build": RUN,
        "finance.dashboard_cash": RUN,
        "logistics.build": RUN,
        "logistics.dashboard_stock": RUN,
        "sales.build": RUN,
        "records": RUN,
    }


# ── ④ 화면 탭 입구 · 공유 입구 ──────────────────────────────────────────


def _잡는_build(잡은: list[Any]):
    def 대역(*args: Any, **kwargs: Any) -> Any:
        #  매입 라우터는 실행 ID 를 둘째 위치 인자로, 나머지는 키워드로 넘긴다.
        잡은.append(kwargs["sim_run_id"] if "sim_run_id" in kwargs else args[1])
        raise _멈춤

    return 대역


#: (모듈, 바꿀 함수 이름, 주소, 더 싣는 쿼리)
_TABS = [
    (dashboard_routes, "build", "/dashboard", {}),
    (finance_routes, "build", "/finance", {"state": "base"}),
    (logistics_routes, "build_result", "/logistics", {"pane": "summary"}),
    (sales_routes, "build", "/sales", {}),
    (purchase_routes, "build", "/purchase", {}),
]


@pytest.mark.parametrize("module,name,path,extra", _TABS, ids=[tab[2] for tab in _TABS])
def test_화면_탭_입구는_비운_실행만_그때의_기준값으로_채운다(
    monkeypatch, module, name, path, extra
):
    잡은: list[Any] = []
    monkeypatch.setattr(module, name, _잡는_build(잡은))
    monkeypatch.setattr(settings, "SHOWN_SIM_RUN_ID", SUBSTITUTE)
    app = FastAPI()
    app.include_router(module.router)
    client = TestClient(app, raise_server_exceptions=False)
    params = {"as_of": AS_OF.isoformat(), **extra}

    client.get(path, params=params)
    client.get(path, params={**params, "sim_run_id": "SIM-OTHER"})

    assert 잡은 == [SUBSTITUTE, "SIM-OTHER"]


def test_공유_입구는_그때의_기준값_하나만_준다(monkeypatch):
    app = FastAPI()
    app.include_router(console_routes.router)
    client = TestClient(app)

    assert client.get("/console/shown-run").json() == {"sim_run_id": settings.SHOWN_SIM_RUN_ID}
    monkeypatch.setattr(settings, "SHOWN_SIM_RUN_ID", SUBSTITUTE)
    assert client.get("/console/shown-run").json() == {"sim_run_id": SUBSTITUTE}


# ── ⑤ 프론트 기준일 ─────────────────────────────────────────────────────


def test_프론트_기준일이_SHOWN_AS_OF_와_같다():
    text = _DEMO_AS_OF.read_text(encoding="utf-8")
    values = re.findall(r'\?\?\s*"(\d{4}-\d{2}-\d{2})"', text)
    assert len(values) == 1, f"demo_as_of.ts 에서 `?? \"YYYY-MM-DD\"` 를 하나로 못 찾았다: {values}"
    assert values[0] == SHOWN_AS_OF.isoformat(), (
        f"프론트 기준일 {values[0]} 이 백엔드 SHOWN_AS_OF {SHOWN_AS_OF} 와 갈렸다"
    )


# ── ⑥ 두 번째 주인 없음 ──────────────────────────────────────────────


def _shown_problems(source: str) -> list[str]:
    """`SHOWN_*` 대입이 «모듈 맨 위에서 한 번 · 글자 그대로» 가 아닌 자리.

    ```text
    SHOWN_SIM_RUN_ID   문자열 하나 ("SIM-…")
    SHOWN_AS_OF        date(연, 월, 일) — 인자 셋이 모두 정수 글자
    ```
    """
    tree = ast.parse(source)
    problems: list[str] = []
    seen: dict[str, ast.expr] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if not (isinstance(target, ast.Name) and target.id.startswith("SHOWN_")):
                continue
            if target.id in seen or node not in tree.body or node.value is None:
                problems.append(f"{node.lineno}: {target.id} 를 다시 · 안쪽에서 정한다")
            seen[target.id] = node.value
    if set(seen) != {"SHOWN_SIM_RUN_ID", "SHOWN_AS_OF"}:
        problems.append(f"SHOWN_* 이름이 둘이 아니다: {sorted(seen)}")
    run = seen.get("SHOWN_SIM_RUN_ID")
    if not (isinstance(run, ast.Constant) and isinstance(run.value, str)):
        problems.append("SHOWN_SIM_RUN_ID 가 글자 그대로의 문자열이 아니다")
    as_of = seen.get("SHOWN_AS_OF")
    if not (
        isinstance(as_of, ast.Call)
        and isinstance(as_of.func, ast.Name)
        and as_of.func.id == "date"
        and not as_of.keywords
        and len(as_of.args) == 3
        and all(isinstance(a, ast.Constant) and isinstance(a.value, int) for a in as_of.args)
    ):
        problems.append("SHOWN_AS_OF 가 date(연, 월, 일) 글자 그대로가 아니다")
    return problems


def test_shown_run_is_two_literal_constants():
    """환경변수 덮어쓰기 같은 두 번째 주인을 만들지 않는다.

    `app/core/settings.py` 는 다른 설정을 환경변수로 읽으므로 파일 전체가 아니라 두 값의
    대입문을 본다 (`_shown_problems`).
    """
    assert {name for name in vars(settings) if name.startswith("SHOWN_")} == {
        "SHOWN_SIM_RUN_ID",
        "SHOWN_AS_OF",
    }
    assert _shown_problems(Path(settings.__file__).read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "planted",
    [
        (
            'SHOWN_SIM_RUN_ID = os.getenv("SHOWN_SIM_RUN_ID", "SIM-X")\n'
            "SHOWN_AS_OF = date(2026, 9, 17)\n"
        ),
        (
            'SHOWN_SIM_RUN_ID = "SIM-X"\n'
            'SHOWN_AS_OF = date.fromisoformat(os.environ["SHOWN_AS_OF"])\n'
        ),
        (
            'SHOWN_SIM_RUN_ID = "SIM-X"\n'
            "SHOWN_AS_OF = date(2026, 9, 17)\n"
            "def _override():\n"
            "    global SHOWN_SIM_RUN_ID\n"
            '    SHOWN_SIM_RUN_ID = "SIM-Y"\n'
        ),
        (
            'SHOWN_SIM_RUN_ID = "SIM-X"\n'
            "SHOWN_AS_OF = date(2026, 9, 17)\n"
            'SHOWN_EXTRA = "SIM-Z"\n'
        ),
    ],
    ids=["환경변수_실행", "환경변수_기준일", "함수_안_재대입", "세_번째_이름"],
)
def test_shown_run_check_catches_planted_second_owners(planted):
    """위 검사가 공짜 초록이 아닌지 — 두 번째 주인을 심으면 문제가 하나 이상 나온다."""
    assert _shown_problems(planted)


# ── ⑦ 기준값을 읽는 자리 · 채우는 자리 ─────────────────────────────────

#: 요청이 비운 실행 ID 를 채우는 HTTP 입구. 이 밖에서 채우는 함수를 부르면 안쪽에 두 번째
#: 기준이 생긴다.
_FILLING_ENTRIES = {
    "api/console/routes.py",
    "api/dashboard/routes.py",
    "api/finance/routes.py",
    "api/logistics/routes.py",
    "api/master/ask.py",
    "api/purchase/routes.py",
    "api/sales/routes.py",
}
_FILL_NAMES = {"screen_sim_run_id", "shown_sim_run_id"}
_SETTINGS = "core/settings.py"


def _names_in(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
    return names


def _reference_problems(files: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for rel, source in files.items():
        if rel == _SETTINGS:
            continue
        names = _names_in(ast.parse(source))
        if "SHOWN_SIM_RUN_ID" in names:
            problems.append(f"{rel}: 기준값을 직접 읽는다")
        if rel not in _FILLING_ENTRIES and names & _FILL_NAMES:
            problems.append(f"{rel}: HTTP 입구 밖에서 기준값으로 채운다")
    return problems


def _app_sources() -> dict[str, str]:
    return {
        path.relative_to(_APP_DIR).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(_APP_DIR.rglob("*.py"))
        if "__pycache__" not in path.parts
    }


def test_기준값은_설정_모듈만_읽고_채우기는_HTTP_입구만_한다():
    sources = _app_sources()
    assert "api/master/ask.py" in sources and "master/service/ask.py" in sources
    assert _reference_problems(sources) == []
    #  채우는 입구가 실제로 채우는 함수를 부른다 — 목록이 낡으면 여기서 드러난다.
    for rel in _FILLING_ENTRIES:
        assert _names_in(ast.parse(sources[rel])) & _FILL_NAMES, rel


@pytest.mark.parametrize(
    "rel,source",
    [
        ("master/service/ask.py", "from app.core.settings import SHOWN_SIM_RUN_ID\n"),
        (
            "api/finance/presenter.py",
            "from app.core import settings\nx = settings.SHOWN_SIM_RUN_ID\n",
        ),
        ("master/service/ask_domain_actions.py", "x = screen_sim_run_id(None)\n"),
    ],
    ids=["서비스_import", "presenter_속성", "서비스_채우기"],
)
def test_기준값_자리_검사는_심어_둔_사본을_잡는다(rel, source):
    assert _reference_problems({rel: source})


# ── ⑧ 프론트 · 빌드 설정 ────────────────────────────────────────────────

#: 실행 ID 를 따로 정하던 자리. 화면은 백엔드 공유 입구가 준 값만 쓴다.
_FRONT_RUN_SOURCES = ("NEXT_PUBLIC_SIM_RUN_ID", "haetdeul.sim_run_id", "FINANCE_SALES_SIM_RUN_ID")
#: 하루 시뮬레이션의 실행 허용 목록. 실행할 수 있는지를 정하는 조건이지 화면 기준값이 아니다.
_DAY_ADVANCE = "src/components/console/DayAdvance.tsx"


def _front_files() -> dict[str, str]:
    front = _REPO / "frontend"
    files = {
        path.relative_to(front).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted((front / "src").rglob("*"))
        if path.suffix in {".ts", ".tsx"}
    }
    for extra in (
        front / "Dockerfile",
        _REPO / "compose.yml",
        _REPO / ".github" / "workflows" / "ci-cd.yml",
    ):
        files[extra.relative_to(_REPO).as_posix()] = extra.read_text(encoding="utf-8")
    return files


def _front_problems(files: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for rel, text in files.items():
        problems += [f"{rel}: {name}" for name in _FRONT_RUN_SOURCES if name in text]
        if rel != _DAY_ADVANCE:
            problems += [f"{rel}: {hit}" for hit in re.findall(r'"SIM-[A-Z0-9-]+"', text)]
    return problems


def test_프론트와_빌드_설정에_실행_ID_를_따로_정하는_자리가_없다():
    files = _front_files()
    assert "src/lib/run_context.ts" in files and _DAY_ADVANCE in files
    assert _front_problems(files) == []


@pytest.mark.parametrize(
    "rel,text",
    [
        ("src/lib/x.ts", 'export const RUN = "SIM-MENTOR-0918";\n'),
        ("src/lib/x.ts", "const SEED = process.env.NEXT_PUBLIC_SIM_RUN_ID;\n"),
        ("src/lib/x.ts", 'localStorage.getItem("haetdeul.sim_run_id");\n'),
        ("frontend/Dockerfile", "ARG NEXT_PUBLIC_SIM_RUN_ID=SIM-MENTOR-0918\n"),
    ],
    ids=["코드_상수", "빌드_값", "브라우저_저장", "빌드_인자"],
)
def test_프론트_검사는_심어_둔_출처를_잡는다(rel, text):
    assert _front_problems({rel: text})
