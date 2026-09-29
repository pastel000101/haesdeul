"""구조 검사가 함께 쓰는 import 스캐너 — 원문을 AST 로 읽어 **어느 모듈을 들이는지** 모은다.

★ **숨긴 import 도 본다.** 함수 안 import · `import a.b` · `from a import b` ·
  문자열로 부르는 `importlib.import_module("a.b")` · `__import__("a.b")`.
  검색 결과를 0 으로 만들려고 import 를 숨기는 길을 막는다.
★ 주석 · docstring · 메시지 문구의 경로 언급은 의존이 아니라 대상이 아니다.
★ `if TYPE_CHECKING:` 안의 import 도 센다 — 실행 중에 안 불러와도 그 방향을 안다는 뜻이다.

2026-09-29 재구성 BL-011 의 부서 → 마스터 검사에서 쓰던 것을 BL-012(마스터 ↔ 화면 ·
domain) 검사와 함께 쓰려고 이 파일로 뺐다.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

_DYNAMIC_IMPORTERS = frozenset({"import_module", "__import__"})


def module_refs(source: str) -> Iterator[tuple[int, str, list[str]]]:
    """import 자리마다 `(줄, 모양, 들이는 모듈 이름들)`.

    `from a.b import c` 는 `a.b` 와 `a.b.c` 를 함께 낸다 — `c` 가 하위 모듈일 수 있다
    (`from app import master` · `from app.master.domain import plan_state`).
    """
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
            yield node.lineno, f"from {node.module}", names
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, f"import {alias.name}", [alias.name]
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            first = node.args[0]
            if (
                name in _DYNAMIC_IMPORTERS
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
            ):
                yield node.lineno, f"{name}({first.value!r})", [first.value]


def is_under(module: str, package: str) -> bool:
    return module == package or module.startswith(package + ".")


def imports_of(source: str, package: str) -> list[str]:
    """그 원문이 `package`(또는 그 아래)를 들이는 자리. `["12: from app.master.envelope"]` 모양."""
    return [
        f"{line}: {shape}"
        for line, shape, names in module_refs(source)
        if any(is_under(name, package) for name in names)
    ]


def python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
