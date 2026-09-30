"""재무 패키지 구조 검증."""

from __future__ import annotations

import ast
import collections
import importlib
import pathlib

import pytest

import app

#: ★ 2026-10-01 재구성 BL-022: 앱 소스 자리는 패키지에서 얻는다. 작업 폴더 기준
#:   `pathlib.Path("app/finance")` 는 backend 밖에서 돌리면 빈 목록을 훑어 아래 검사들이 아무것도
#:   안 보고 통과했다(모듈 이름은 backend 기준 상대 경로로 짓는다).
FINANCE = pathlib.Path(app.__file__).parent / "finance"
_BACKEND = FINANCE.parent.parent


def _modules() -> list[str]:
    out = []
    for path in sorted(FINANCE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        out.append(".".join(path.relative_to(_BACKEND).with_suffix("").parts))
    return out


# ---------------------------------------------------------------------------
# 외부/기존 진입점 호환
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module",
    [
        # 재무 밖에서 실제로 import 하는 경로 — 절대 깨뜨리지 않는다.
        # ★ 2026-09-29 재구성 BL-014: `app.finance.db` 는 역할별로 나누고 지웠다(마스터
        #   조회 · 쓰기 헬퍼는 `app.master.db`, 재무 SQL 은 `app.finance.repository`).
        #   아래 검사가 되살아나지 않는지 본다.
        #   2026-09-30 재구성 BL-019: 라우터(`app.finance.router`)는 `app/api/finance/` 로
        #   옮겼다 — 옛 경로가 되살아나지 않는지는 `tests/architecture/test_http_entries.py`.
        "app.finance.adapter",       # api/finance/agent.py (finance_port) · 마스터 파트 등록소
        "app.finance.schemas",
    ],
)
def test_externally_imported_modules_still_resolve(module):
    importlib.import_module(module)


def test_the_finance_db_entry_is_not_left_behind():
    """★ 2026-09-29 재구성 BL-014: 옛 입구를 다시 내보내는 중계 모듈을 남기지 않는다."""
    import importlib.util

    for module in ("app.finance.db", "app.finance.execution", "app.finance.application"):
        assert importlib.util.find_spec(module) is None, module


def test_finance_port_and_controller_still_resolve():
    from app.finance.adapter import finance_port
    from app.finance.service.agent import FinanceAgentController

    assert callable(finance_port)
    assert FinanceAgentController is not None


def test_router_exposes_the_same_endpoints():
    #  ★ 2026-09-30 재구성 BL-019: 재무 라우트는 `app/api/finance/<자원>.py` 여섯 파일이다.
    from app.api.finance import (
        agent,
        cash_adjustments,
        collections,
        credit_limits,
        expenses,
        runs,
    )

    routers = (credit_limits, expenses, collections, cash_adjustments, agent, runs)
    paths = {route.path for module in routers for route in module.router.routes}
    assert {"/finance/agent", "/finance/runs"} <= paths
    assert "/finance/sales" not in paths


def test_tool_registry_keeps_its_public_names():
    """Registry 이름은 Harness 가 소유한다 — **디스패치는 실행 통제의 일부다.**"""
    from app.finance.service import harness

    for name in ("PRE_PURCHASE_TOOLS", "SCENARIO_VALIDATION_TOOLS", "FinanceToolRegistry"):
        assert hasattr(harness, name), name


def test_schedule_helpers_live_with_the_capability_that_owns_them():
    """지급 일정 재구성은 **그것을 쓰는 mode** 옆에 산다 — 시나리오 판정이 유일한 소비자다.

    ★ 2026-09-29 재구성 BL-014: DataPort 를 부르지 않는 계산은 `domain/scenario.py` 로 옮기고
      (서비스가 부르는 셋은 밑줄을 뗐다), Tool 실행 순서는 `service/capabilities/scenario.py`
      에 남았다. 앱 안에서 그 계산을 들이는 곳은 여전히 시나리오 capability 하나다.
    """
    from app.finance.domain import scenario

    for name in ("scenario_schedule", "schedule_events", "calculate_schedule_cap"):
        assert hasattr(scenario, name), name

    importers = sorted(
        str(path.relative_to(_BACKEND)).replace("\\", "/")
        for path in FINANCE.parent.rglob("*.py")
        if "__pycache__" not in path.parts
        and any(
            isinstance(node, ast.ImportFrom) and node.module == "app.finance.domain.scenario"
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        )
    )
    assert importers == ["app/finance/service/capabilities/scenario.py"]


def test_every_finance_module_imports():
    """구조를 옮긴 뒤 **한 모듈도 죽지 않았는지** 통째로 확인한다."""
    for module in _modules():
        importlib.import_module(module)


# ---------------------------------------------------------------------------
# 책임 분리
# ---------------------------------------------------------------------------


def test_registry_is_a_thin_dispatcher():
    """🔴 예전에는 디스패처가 컨텍스트·두 mode·일정·Evidence 를 다 들었다.

    ★ Harness 안으로 들어왔어도 **디스패치는 여전히 얇다.** mode 검사 하나와 위임뿐이다.
    """
    source = (FINANCE / "service" / "harness.py").read_text(encoding="utf-8")
    registry = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "FinanceToolRegistry"
    )
    methods = {n.name for n in registry.body if isinstance(n, ast.FunctionDef)}
    assert methods == {"__init__", "names_for", "execute"}


def test_capabilities_are_split_by_mode():
    from app.finance.service.capabilities import procurement, scenario

    for name in (
        "assess_finance_position",
        "project_cashflow",
        "calculate_purchase_finance_cap",
        "analyze_payment_pressure",
    ):
        assert hasattr(procurement, name), name
    for name in ("evaluate_purchase_scenario", "validate_amount_adjustment"):
        assert hasattr(scenario, name), name


def test_tool_names_are_unchanged():
    """Planner 계약이다 — 이름이 바뀌면 모델이 고를 수 없다."""
    from app.finance.service.harness import PRE_PURCHASE_TOOLS, SCENARIO_VALIDATION_TOOLS

    assert PRE_PURCHASE_TOOLS == frozenset(
        {
            "assess_finance_position",
            "project_cashflow",
            "calculate_purchase_finance_cap",
            "analyze_payment_pressure",
        }
    )
    assert SCENARIO_VALIDATION_TOOLS == frozenset(
        {"evaluate_purchase_scenario", "validate_amount_adjustment"}
    )


def test_no_capability_is_registered_twice():
    from app.finance.service.harness import (
        _CAPABILITIES,
        PRE_PURCHASE_TOOLS,
        SALES_VALIDATION_TOOLS,
        SCENARIO_VALIDATION_TOOLS,
    )

    # ★ 세 mode 의 Tool 집합은 서로 겹치지 않는다 — 매입 판정 공식이 판매 회신에
    #   실리거나 그 반대가 되는 길을 막는다.
    assert not PRE_PURCHASE_TOOLS & SCENARIO_VALIDATION_TOOLS
    assert not PRE_PURCHASE_TOOLS & SALES_VALIDATION_TOOLS
    assert not SCENARIO_VALIDATION_TOOLS & SALES_VALIDATION_TOOLS
    assert (
        set(_CAPABILITIES)
        == PRE_PURCHASE_TOOLS | SCENARIO_VALIDATION_TOOLS | SALES_VALIDATION_TOOLS
    )
    # 한 구현이 두 이름에 걸리면 어느 쪽을 고쳤는지 알 수 없다.
    assert len({id(fn) for fn in _CAPABILITIES.values()}) == len(_CAPABILITIES)


def test_no_duplicate_business_definitions_were_introduced():
    """같은 규칙이 두 벌이면 한쪽만 고쳐지고, 그때 갈리는 것은 판정이다."""
    seen: dict[str, list[str]] = collections.defaultdict(list)
    for path in sorted(FINANCE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.FunctionDef | ast.ClassDef):
                seen[node.name].append(str(path))

    duplicates = {name: paths for name, paths in seen.items() if len(paths) > 1}
    assert set(duplicates) <= {"project_cashflow"}, duplicates


def test_deterministic_calculations_stay_in_tools():
    """계산은 `tools.py` 소유다 — capability 로 복사되지 않았다."""
    from app.finance.domain import tools

    for name in (
        "project_cashflow",
        "calculate_finance_cap",
        "derive_cash_priority",
        "build_payroll_schedule",
    ):
        assert hasattr(tools, name), name

    capability = (FINANCE / "service" / "capabilities" / "procurement.py").read_text(
        encoding="utf-8"
    )
    # cap 공식이 capability 안에서 다시 구현되지 않았다.
    assert "ROUND_FLOOR" not in capability
    assert "calculate_finance_cap(" in capability


def test_rules_are_not_absorbed_into_agent_or_adapter():
    """판정은 `rules.py` 가 소유한다."""
    from app.finance.domain import rules

    assert hasattr(rules, "classify_base_stress")
    for module in ("adapter.py", "service/agent.py"):
        source = (FINANCE / module).read_text(encoding="utf-8")
        assert "def classify_base_stress" not in source
