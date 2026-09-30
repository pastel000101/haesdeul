"""LLM 호출의 자리 — 2026-09-30 재구성 BL-020.

```text
app/core/llm/providers.py   외부 LLM 호출(urlopen · anthropic · openai SDK · Gemini 주소)의
                            유일한 자리
app/core/llm/runtime.py     실행 골격(run_with_fallback) · `<PREFIX>_` 우선 설정 읽기
부서 llm/                   지시문 · 응답 스키마 · 검증기 · 부서 설정값
                            · 부서마다 다른 재시도 · 대체
app/contracts/envelope.py   상태 네 값(LLMStatus)의 유일한 정의
```

이 파일이 잠그는 것:

1. 외부 LLM 호출 코드는 `app/core/llm/providers.py` 에만 있다 — `urlopen` · `anthropic` · `openai` ·
   Gemini 주소 · `:generateContent` · `/api/chat` 을 다른 모듈이 쓰지 않는다.
2. `app/core/llm` 은 `app.core` 밖(부서 · 마스터 · 계약 · 화면)을 들이지 않는다.
3. 상태 네 값의 Literal 은 봉투에 한 번만 적는다 — 부서가 같은 네 값을 다시 적지 않는다.
4. 환경변수를 참 · 거짓으로 읽는 값 집합(`{"1", "true", "yes", "on"}`)은 core 한 곳이다.

★ 스캐너는 `import_scan.py` 와 AST 다. 검사마다 **심어 둔 모양을 스스로 잡는지**도 잰다 —
  0건을 세는 검사가 공짜로 초록이 되지 않게.
"""

from __future__ import annotations

import ast
from pathlib import Path

from import_scan import is_under, module_refs, python_files

import app

_APP = Path(app.__file__).parent
_PROVIDERS = "core/llm/providers.py"
_ENVELOPE = "contracts/envelope.py"
_RUNTIME = "core/llm/runtime.py"

#: 외부 LLM 을 부른다는 표시 — 글자로 적힌 주소 조각과 SDK 이름.
_LLM_ADDRESS_MARKS = ("generativelanguage.googleapis.com", ":generateContent", "/api/chat")
_LLM_SDKS = ("anthropic", "openai")
_STATUSES = frozenset({"SUCCESS", "SKIPPED_TEMPLATE", "FALLBACK", "DISABLED"})
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _rel(path: Path) -> str:
    return path.relative_to(_APP).as_posix()


def _llm_call_marks(source: str) -> list[str]:
    """외부 LLM 호출 표시 — `urlopen` 참조 · SDK import · 주소 조각 문자열. `["12: urlopen"]` 모양.

    docstring · 주석은 보지 않는다(문자열 상수 중 모듈 · 함수 · 클래스의 첫 문장은 뺀다).
    """
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    marks = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr == "urlopen") or (
            isinstance(node, ast.Name) and node.id == "urlopen"
        ):
            marks.append(f"{node.lineno}: urlopen")
        elif isinstance(node, ast.alias) and node.name == "urlopen":
            marks.append("import urlopen")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and any(mark in node.value for mark in _LLM_ADDRESS_MARKS)
        ):
            marks.append(f"{node.lineno}: {node.value[:40]!r}")
    marks += [
        f"{line}: {shape}"
        for line, shape, names in module_refs(source)
        if any(is_under(name, sdk) for name in names for sdk in _LLM_SDKS)
    ]
    return sorted(marks)


def _status_literals(source: str) -> list[int]:
    """상태 네 값을 그대로 적은 `Literal[...]` 의 줄."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Subscript) and getattr(node.value, "id", None) == "Literal"):
            continue
        elements = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        values = {e.value for e in elements if isinstance(e, ast.Constant)}
        if values == _STATUSES:
            lines.append(node.lineno)
    return lines


def _truthy_sets(source: str) -> list[int]:
    """`{"1", "true", "yes", "on"}` 과 같은 값 집합(집합 · frozenset · 튜플 · 목록 글자)의 줄."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Set | ast.Tuple | ast.List):
            values = {e.value for e in node.elts if isinstance(e, ast.Constant)}
            if len(node.elts) == len(_TRUE_VALUES) and values == _TRUE_VALUES:
                lines.append(node.lineno)
    return lines


# ---------------------------------------------------------------------------
# 1. 외부 LLM 호출은 core/llm/providers.py 한 곳
# ---------------------------------------------------------------------------


def test_external_llm_calls_live_only_in_core_providers():
    found = {
        _rel(path): marks
        for path in python_files(_APP)
        if (marks := _llm_call_marks(_read(path)))
    }
    assert set(found) == {_PROVIDERS}, found


def test_the_providers_file_really_holds_the_calls():
    """★ 스캐너가 제 자리를 읽고 있는지 — providers 에 urlopen · SDK · 주소가 **있어야** 한다."""
    marks = " ".join(_llm_call_marks(_read(_APP / _PROVIDERS)))
    expected = ("urlopen", "import anthropic", "import openai", ":generateContent", "/api/chat")
    for mark in expected:
        assert mark in marks, (mark, marks)


def test_the_llm_call_scan_catches_planted_shapes():
    assert _llm_call_marks("import urllib.request\nurllib.request.urlopen(r)\n")
    assert _llm_call_marks("from urllib.request import urlopen\n")
    assert _llm_call_marks("def f():\n    import anthropic\n")
    assert _llm_call_marks("from openai import OpenAI\n")
    assert _llm_call_marks('URL = "https://generativelanguage.googleapis.com/v1beta"\n')
    assert _llm_call_marks('u = f"{base}/models/{m}:generateContent"\n')
    assert _llm_call_marks('u = f"{base}/api/chat"\n')
    assert not _llm_call_marks('"""모듈 설명 — `/api/chat` 을 부른다."""\n')


# ---------------------------------------------------------------------------
# 2. core/llm 은 부서를 모른다
# ---------------------------------------------------------------------------


def _outside_core(source: str) -> list[str]:
    return sorted(
        f"{line}: {shape}"
        for line, shape, names in module_refs(source)
        if any(is_under(name, "app") and not is_under(name, "app.core") for name in names)
    )


def test_core_llm_imports_nothing_outside_core():
    files = python_files(_APP / "core" / "llm")
    assert {_rel(path) for path in files} >= {_PROVIDERS, _RUNTIME}
    found = {_rel(path): hits for path in files if (hits := _outside_core(_read(path)))}
    assert found == {}


def test_the_core_scan_catches_planted_imports():
    assert _outside_core("from app.finance.llm.client import finance_model\n")
    assert _outside_core("def f():\n    from app.contracts.envelope import LLMStatus\n")
    assert _outside_core("importlib.import_module('app.master.llm.runtime')\n")
    assert not _outside_core("from app.core.settings import ENV_FILE\n")


# ---------------------------------------------------------------------------
# 3. 상태 네 값은 봉투에 한 번
# ---------------------------------------------------------------------------


def test_the_status_literal_is_written_once_in_the_envelope():
    found = {
        _rel(path): lines
        for path in python_files(_APP)
        if (lines := _status_literals(_read(path)))
    }
    assert found == {_ENVELOPE: found.get(_ENVELOPE, [])}, found
    assert len(found[_ENVELOPE]) == 1


def test_the_status_scan_catches_planted_copies():
    assert _status_literals('S = Literal["SUCCESS", "SKIPPED_TEMPLATE", "FALLBACK", "DISABLED"]\n')
    assert _status_literals(
        'class M:\n    s: Literal["DISABLED", "FALLBACK", "SUCCESS", "SKIPPED_TEMPLATE"] = "X"\n'
    )
    assert not _status_literals('S = Literal["SUCCESS", "FALLBACK"]\n')


# ---------------------------------------------------------------------------
# 4. 참 · 거짓 읽기의 값 집합은 core 한 곳
# ---------------------------------------------------------------------------


def test_truthy_env_values_live_only_in_core_runtime():
    found = {
        _rel(path): lines for path in python_files(_APP) if (lines := _truthy_sets(_read(path)))
    }
    assert set(found) == {_RUNTIME}, found


def test_the_truthy_scan_catches_planted_readers():
    assert _truthy_sets('ok = v.strip().lower() in {"1", "true", "yes", "on"}\n')
    assert _truthy_sets('T = frozenset({"on", "yes", "true", "1"})\n')
    assert not _truthy_sets('off = v in {"0", "false", "False"}\n')
