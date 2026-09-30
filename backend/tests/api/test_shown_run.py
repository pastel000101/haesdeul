"""화면이 읽는 실행은 `app/core/settings.py` 의 `SHOWN_SIM_RUN_ID` · `SHOWN_AS_OF` 한 자리다.

🟢 2026-09-29 (재구성 BL-012) 전에는 그 두 값이 `app/api/shown_run.py` 한 파일에 있었다.
   마스터(`ask_service`)도 같은 값을 읽어 `master → api` 의존이 생겼으므로 공용 설정으로
   옮겼다. 값 · 뜻 · 쓰는 곳은 그대로라 아래 ②~⑤ 는 같은 것을 잰다.

🔴 **실 DB 에 닿지 않는다.** 서비스 함수와 커넥션을 대역으로 바꿔 «무엇을 넘기는가» 만 본다.

```text
① app/api 아래 파이썬 소스에 BURN_IN_SIM_RUN_ID 이름이 한 번도 안 쓰인다 (AST)
② 재무 · 판매 · 물류 build 가 서비스에 SHOWN_SIM_RUN_ID 를 넘긴다
③ 대시보드가 매입 build 에 sim_run_id=SHOWN_SIM_RUN_ID 를 넘긴다
④ 매입 라우터: 쿼리 없음 → SHOWN · 쿼리 있음 → 준 값
⑤ 프론트 기준일 코드값 == SHOWN_AS_OF
⑥ 두 값은 글자 그대로 적은 상수다 — 환경변수 등 두 번째 주인이 없다
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

from app.api.dashboard import presenter as dashboard_presenter
from app.api.finance import presenter as finance_presenter
from app.api.logistics import presenter as logistics_presenter
from app.api.purchase import routes as purchase_routes
from app.api.sales import presenter as sales_presenter
from app.core import settings
from app.core.settings import SHOWN_AS_OF, SHOWN_SIM_RUN_ID
from app.logistics.readmodel import console as console_readmodel

_BACKEND = Path(__file__).resolve().parents[2]
_API_DIR = _BACKEND / "app" / "api"
_DEMO_AS_OF = _BACKEND.parent / "frontend" / "src" / "lib" / "demo_as_of.ts"
_BURN_IN = "BURN_IN_SIM_RUN_ID"

AS_OF = date(2026, 1, 26)


class _멈춤(Exception):
    """넘긴 값을 잡은 뒤 계산을 더 진행하지 않으려고 던진다."""


# ── ① 스캔 잠금 ─────────────────────────────────────────────────────────


#: 마스터 · Critic 의 HTTP 입구 — 화면이 아니다(2026-09-30 재구성 BL-019 에 `app/master/router.py` ·
#: `master/critic/router.py` 에서 `app/api/` 로 옮겼다). 하루 단계 입구는 번인 축을 **거절**하려고
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
    """🔴 `import` · 이름 · 속성 어디로도 번인 상수를 쓰지 않는다. 문자열은 안 본다."""
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

    #  ★ 마스터 HTTP 입구에서는 `_walk_axis`(번인 축 거절)와 그 import 한 줄뿐이다.
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


def test_재무_build_가_보는_실행을_넘긴다(monkeypatch):
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
        finance_presenter.build(AS_OF, "base")
    assert 잡은 == [("dashboard", SHOWN_SIM_RUN_ID), ("cashflow", SHOWN_SIM_RUN_ID)]


def test_판매_build_가_보는_실행을_넘긴다(monkeypatch):
    잡은: list[str] = []

    def 대시보드(*, sim_run_id: str, as_of: date) -> Any:
        잡은.append(sim_run_id)
        raise _멈춤

    monkeypatch.setattr(sales_presenter, "get_sales_dashboard", 대시보드)
    with pytest.raises(_멈춤):
        sales_presenter.build(AS_OF)
    assert 잡은 == [SHOWN_SIM_RUN_ID]


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
    #  ★ Runtime 읽기는 **한 판에 한 번** 이고 두 콘솔이 나눠 쓴다 (2026-09-15).
    monkeypatch.setattr(console_readmodel, "load_console_runtime", 기록("runtime", None))
    #  ★ 그날 예약(Historical)도 한 판에 한 번 — 재고·출고 콘솔이 나눠 쓴다 (#760).
    monkeypatch.setattr(console_readmodel, "reservation_state_at", 기록("reservations", ()))
    monkeypatch.setattr(
        console_readmodel, "get_inbound_console", 기록("inbound", SimpleNamespace(in_transit=[]))
    )
    monkeypatch.setattr(console_readmodel, "get_inventory_console", 기록("inventory", None))
    monkeypatch.setattr(console_readmodel, "get_outbound_console", 기록("outbound", None))
    #  ★ **Exception 두 조회도 같은 축에 선다** (#675). 창고 배치(`warehouse`) 는
    #    발표 화면에서 빠져 부르지 않는다.
    monkeypatch.setattr(console_readmodel, "live_exceptions_at", 기록("live_exceptions", None))
    monkeypatch.setattr(
        console_readmodel, "resolved_exceptions_on", 기록("resolved_exceptions", ())
    )
    return 잡은


def test_물류_build_가_보는_실행을_넘기고_출처에_적는다(monkeypatch):
    잡은 = _물류_대역(monkeypatch)
    #  대역 콘솔이 빈 값이라 판 조립은 실패한다. 넘긴 축과 출처 글만 본다.
    result = logistics_presenter.build_result(AS_OF, "summary")
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
    assert {run for _, run in 잡은} == {SHOWN_SIM_RUN_ID}
    assert f"보고 있는 실행: {SHOWN_SIM_RUN_ID} · 기준일: {AS_OF}" in (result.tab.source.note or "")


def test_물류_재고그래프가_보는_실행을_넘긴다(monkeypatch):
    잡은 = _물류_대역(monkeypatch)
    logistics_presenter.dashboard_stock(10, 5, AS_OF)
    names = [name for name, _ in 잡은]
    assert names == ["coverage", "onhand", "days", "runtime", "inbound"]
    assert {run for _, run in 잡은} == {SHOWN_SIM_RUN_ID}


# ── ③ 대시보드 → 매입 ───────────────────────────────────────────────────


def test_대시보드가_매입_build_에_보는_실행을_넘긴다(monkeypatch):
    잡은: list[dict[str, Any]] = []

    def 매입(as_of: date, *args: Any, **kwargs: Any) -> Any:
        잡은.append({"args": args, "kwargs": kwargs})
        raise _멈춤

    monkeypatch.setattr(
        dashboard_presenter.forecast_presenter, "build", lambda *a, **k: SimpleNamespace()
    )
    monkeypatch.setattr(dashboard_presenter.purchase_presenter, "build", 매입)
    with pytest.raises(_멈춤):
        dashboard_presenter.build(AS_OF)
    #  🔵 `window_days=0` — 대시보드는 도착일을 안 읽는다 (`#740` 의 인자 · 2026-09-16).
    #     여기서 같이 잠근다: 축이 빠지는 것도, 창이 조용히 넓어지는 것도 사고다.
    assert 잡은 == [
        {"args": (), "kwargs": {"sim_run_id": SHOWN_SIM_RUN_ID, "window_days": 0}}
    ]


# ── ④ 매입 라우터 ───────────────────────────────────────────────────────


@pytest.fixture
def 매입_라우터(monkeypatch):
    잡은: list[Any] = []

    def 매입(as_of: date, sim_run_id: str | None = None) -> Any:
        잡은.append(sim_run_id)
        raise _멈춤

    monkeypatch.setattr(purchase_routes, "build", 매입)
    app = FastAPI()
    app.include_router(purchase_routes.router)
    return TestClient(app, raise_server_exceptions=False), 잡은


def test_매입_라우터는_축이_없으면_보는_실행을_쓴다(매입_라우터):
    client, 잡은 = 매입_라우터
    client.get("/purchase", params={"as_of": AS_OF.isoformat()})
    assert 잡은 == [SHOWN_SIM_RUN_ID]


def test_매입_라우터는_축을_주면_준_값이_이긴다(매입_라우터):
    client, 잡은 = 매입_라우터
    client.get("/purchase", params={"as_of": AS_OF.isoformat(), "sim_run_id": "SIM-OTHER"})
    assert 잡은 == ["SIM-OTHER"]


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
    """🔴 환경변수 덮어쓰기 같은 두 번째 주인을 만들지 않는다.

    ★ 2026-09-29 까지는 `shown_run.py` 파일에 두 이름만 있고 «environ» 이라는 글자가
      없는지를 봤다. 두 값이 환경변수를 읽는 `app/core/settings.py` 로 옮겨 오면서 그
      파일 전체 대신 **두 값의 대입문**을 본다 (`_shown_problems`).
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
    """★ 위 검사가 공짜 초록이 아닌지 — 두 번째 주인을 심으면 문제가 하나 이상 나온다."""
    assert _shown_problems(planted)
