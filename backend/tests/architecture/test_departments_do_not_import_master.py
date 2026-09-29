"""부서 패키지는 마스터를 import 하지 않는다 (재구성 BL-011 · 설계서 §의존 방향 규칙).

부서(재무 · 물류 · 판매 · 매입 · ML)가 마스터와 주고받는 것은 공용 계약(`app.contracts`)이고,
마스터가 부서를 부르는 길은 등록소다. 2026-09-29 전에는 부서가 봉투 · 약정 · 파트 결과 ·
Critic 검사 id 를 가져오려고 `app.master.*` 를 import 했고(22줄), ML 은 마스터 등록소를
스스로 불렀다. 그것들을 `app/contracts/` 와 마스터 조립 뿌리로 옮긴 뒤 이 검사로 0 을 잠근다.

★ **숨긴 import 도 본다.** 함수 안 import · `import app.master…` · `from app import master` ·
  문자열로 부르는 `importlib.import_module("app.master…")` · `__import__("app.master…")`.
  검색 결과를 0 으로 만들려고 import 를 숨기는 길을 막는다.
★ 주석 · docstring · 메시지 문구의 경로 언급은 의존이 아니라 대상이 아니다.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import app

_APP = Path(app.__file__).parent

#: 부서 패키지. 마스터 · 화면(`api`) · 계약 · 기반(`core`)은 대상이 아니다.
DEPARTMENTS = ("finance", "logistics", "sales", "purchase_agent", "ml")

_MASTER = "app.master"
_DYNAMIC_IMPORTERS = frozenset({"import_module", "__import__"})


def _is_master(module: str) -> bool:
    return module == _MASTER or module.startswith(_MASTER + ".")


def master_imports_in(source: str) -> list[str]:
    """그 원문이 마스터를 들이는 자리. `["12: from app.master.envelope"]` 모양."""
    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            takes_master = node.module == "app" and any(a.name == "master" for a in node.names)
            if _is_master(node.module) or takes_master:
                hits.append(f"{node.lineno}: from {node.module}")
        elif isinstance(node, ast.Import):
            hits.extend(f"{node.lineno}: import {a.name}" for a in node.names if _is_master(a.name))
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            first = node.args[0]
            if (
                name in _DYNAMIC_IMPORTERS
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and _is_master(first.value)
            ):
                hits.append(f"{node.lineno}: {name}({first.value!r})")
    return hits


def _department_files() -> Iterator[Path]:
    for dept in DEPARTMENTS:
        for path in sorted((_APP / dept).rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path


def test_부서는_마스터를_import_하지_않는다():
    found = {
        path.relative_to(_APP).as_posix(): hits
        for path in _department_files()
        if (hits := master_imports_in(path.read_text(encoding="utf-8")))
    }

    assert found == {}, (
        "부서가 마스터를 들인다 — 주고받는 타입이면 `app.contracts` 로, 등록이면 마스터 조립"
        f" 뿌리(`app/master/bootstrap.py`)로 옮긴다: {found}"
    )


def test_스캐너가_부서_파일을_실제로_읽는다():
    """★ 0 개를 읽으면 위 검사가 공짜 초록이 된다. 무엇을 읽었는지를 먼저 잰다."""
    files = list(_department_files())

    assert {path.relative_to(_APP).parts[0] for path in files} == set(DEPARTMENTS)
    assert len(files) > 100, f"부서 파일을 {len(files)}개밖에 못 읽었다"


def test_스캐너가_숨긴_import_모양을_잡는다():
    """심어 둔 여섯 모양이 다 잡히고, 계약 import 와 주석 언급은 안 잡힌다."""
    planted = (
        "import app.master.wiring as w\n"
        "from app import master\n"
        "from app.master.envelope import AgentRequest\n"
        "def later():\n"
        "    from app.master.wiring import register\n"
        "    importlib.import_module('app.master.wiring')\n"
        "    __import__('app.master')\n"
    )

    assert len(master_imports_in(planted)) == 6, master_imports_in(planted)
    assert master_imports_in(
        "from app.contracts.envelope import AgentRequest\n# app.master 를 말만 한다\n"
        "x = 'app/master/transition.py 가 만든다'\n"
    ) == []
