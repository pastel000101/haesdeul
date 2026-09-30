"""Gemini 스키마 변환 — 부서마다 다른 기존 동작을 잠근다 (2026-09-30 BL-020 보완).

BL-020 에서 부서 변환 네 벌(마스터 · 매입 · 판매 · 재무)을 두 벌로 합쳤다가 마스터 · 재무의
동작이 바뀌었다. 지금 보내는 스키마에서는 결과가 같아 기존 검사가 못 잡았다. 되돌린 뒤의 기대값은
옮기기 전(`738ff9ee`) 각 부서 변환을 같은 입력으로 돌려 얻은 결과 · 예외다.

```text
부서    부르는 식                                       못 편 참조    길이 제약   X|null 아닌 anyOf
마스터  gemini_strict_schema(schema)                    TypeError     남김        TypeError
매입    gemini_response_schema(schema)                  KeyError      버림        anyOf 로 둠
판매    gemini_safe_schema(schema)                      TypeError     남김        anyOf 로 둠
재무    gemini_safe_schema(params, inline_refs=False)   펴지 않음     남김        anyOf 로 둠
```

★ 결과는 값과 **키 순서까지** 본다 — 요청 본문 바이트가 그 순서로 나간다.
★ 입력 스키마를 바꾸지 않는지도 본다.
★ 네트워크를 타지 않는다. 부서 경로 검사는 `urlopen` 을 갈아끼워 요청 본문만 본다.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from typing import Any, Self

import pytest

from app.core.llm.providers import gemini_response_schema, gemini_safe_schema, gemini_strict_schema

CONVERTERS: dict[str, Callable[[Any], Any]] = {
    "master": gemini_strict_schema,
    "purchase": gemini_response_schema,
    "sales": gemini_safe_schema,
    "finance": lambda node: gemini_safe_schema(node, inline_refs=False),
}


class Raises:
    """기대 결과가 예외일 때 — 종류와 문장을 그대로 본다.

    `message` 가 `None` 이면 종류만 본다 — 부서 코드가 아니라 파이썬이 낸 문장일 때다.
    """

    def __init__(self, kind: type[Exception], message: str | None) -> None:
        self.kind = kind
        self.message = message


UNCHANGED = object()  # 재무: 입력과 같은 값 · 같은 순서로 돌아온다

LENGTH = {"type": "string", "minLength": 1, "maxLength": 5, "description": "d"}
MISSING_REF = {"type": "object", "properties": {"a": {"$ref": "#/$defs/Missing"}}}
REF_WITH_SIBLINGS = {
    "$defs": {"A": {"type": "string", "description": "inner", "title": "A"}},
    "properties": {"a": {"$ref": "#/$defs/A", "description": "outer", "default": "x"}},
}
NESTED_DEFS = {
    "type": "object",
    "properties": {"x": {"$defs": {"B": {"type": "integer"}}, "$ref": "#/$defs/B"}},
}
NULLABLE_SIBLING_FIRST = {
    "description": "outer",
    "anyOf": [{"type": "string", "description": "inner"}, {"type": "null"}],
}
REF_TARGET_NOT_DICT = {"$defs": {"A": True}, "properties": {"a": {"$ref": "#/$defs/A"}}}
NON_STRING_REF = {"type": "object", "properties": {"a": {"$ref": 5, "type": "string"}}}
TWO_BRANCHES = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
TWO_BRANCHES_AND_NULL = {"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]}
NESTED_OBJECT_ARRAY = {
    "type": "object",
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "title": "Row",
                "properties": {
                    "note": {
                        "anyOf": [{"type": "string", "maxLength": 9}, {"type": "null"}],
                        "default": None,
                    }
                },
            },
        }
    },
}
TOOL_WITH_NESTED_MODEL = {
    "type": "object",
    "$defs": {"Leg": {"type": "object", "properties": {"amount": {"type": "number"}}}},
    "properties": {"legs": {"type": "array", "items": {"$ref": "#/$defs/Leg"}}},
}

_LEG_INLINED = {
    "type": "object",
    "properties": {
        "legs": {
            "type": "array",
            "items": {"type": "object", "properties": {"amount": {"type": "number"}}},
        }
    },
}
_ROWS_SAFE = {
    "type": "object",
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "title": "Row",
                "properties": {
                    "note": {"default": None, "nullable": True, "type": "string", "maxLength": 9}
                },
            },
        }
    },
}

EXPECTED: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
    "length_constraints": (
        LENGTH,
        {
            "master": {"type": "string", "minLength": 1, "maxLength": 5, "description": "d"},
            "purchase": {"type": "string", "description": "d"},
            "sales": UNCHANGED,
            "finance": UNCHANGED,
        },
    ),
    "unresolvable_ref": (
        MISSING_REF,
        {
            "master": Raises(TypeError, "cannot resolve schema reference: #/$defs/Missing"),
            "purchase": Raises(KeyError, "gemini 스키마에서 못 펴는 참조다: #/$defs/Missing"),
            "sales": Raises(TypeError, "cannot resolve schema reference: #/$defs/Missing"),
            "finance": UNCHANGED,
        },
    ),
    "ref_with_siblings": (
        REF_WITH_SIBLINGS,
        {
            "master": {"properties": {"a": {"type": "string", "description": "outer"}}},
            "purchase": {"properties": {"a": {"type": "string", "description": "outer"}}},
            "sales": {
                "properties": {
                    "a": {"type": "string", "description": "outer", "title": "A", "default": "x"}
                }
            },
            "finance": UNCHANGED,
        },
    ),
    "ref_to_nested_defs": (
        NESTED_DEFS,
        {
            "master": Raises(TypeError, "cannot resolve schema reference: #/$defs/B"),
            "purchase": {"type": "object", "properties": {"x": {"type": "integer"}}},
            "sales": Raises(TypeError, "cannot resolve schema reference: #/$defs/B"),
            "finance": UNCHANGED,
        },
    ),
    "ref_target_not_dict": (
        REF_TARGET_NOT_DICT,
        {
            "master": Raises(TypeError, "cannot resolve schema reference: #/$defs/A"),
            "purchase": Raises(TypeError, None),
            "sales": Raises(TypeError, "cannot resolve schema reference: #/$defs/A"),
            "finance": UNCHANGED,
        },
    ),
    "non_string_ref": (
        NON_STRING_REF,
        {
            "master": {"type": "object", "properties": {"a": {"type": "string"}}},
            "purchase": UNCHANGED,
            "sales": UNCHANGED,
            "finance": UNCHANGED,
        },
    ),
    "nullable_anyof_after_sibling": (
        NULLABLE_SIBLING_FIRST,
        {
            "master": {"type": "string", "description": "outer", "nullable": True},
            "purchase": {"description": "inner", "type": "string", "nullable": True},
            "sales": {"description": "inner", "nullable": True, "type": "string"},
            "finance": {"description": "inner", "nullable": True, "type": "string"},
        },
    ),
    "two_branch_anyof": (
        TWO_BRANCHES,
        {
            "master": Raises(
                TypeError,
                "Gemini 로 옮길 수 없는 anyOf 다 (분기 2개): "
                "[{'type': 'string'}, {'type': 'integer'}]",
            ),
            "purchase": UNCHANGED,
            "sales": UNCHANGED,
            "finance": UNCHANGED,
        },
    ),
    "two_branch_anyof_with_null": (
        TWO_BRANCHES_AND_NULL,
        {
            "master": Raises(
                TypeError,
                "Gemini 로 옮길 수 없는 anyOf 다 (분기 2개): "
                "[{'type': 'string'}, {'type': 'integer'}, {'type': 'null'}]",
            ),
            "purchase": {"anyOf": [{"type": "string"}, {"type": "integer"}], "nullable": True},
            "sales": {"nullable": True, "anyOf": [{"type": "string"}, {"type": "integer"}]},
            "finance": {"nullable": True, "anyOf": [{"type": "string"}, {"type": "integer"}]},
        },
    ),
    "nested_object_array": (
        NESTED_OBJECT_ARRAY,
        {
            "master": {
                "type": "object",
                "properties": {
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "note": {"type": "string", "maxLength": 9, "nullable": True}
                            },
                        },
                    }
                },
            },
            "purchase": {
                "type": "object",
                "properties": {
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"note": {"type": "string", "nullable": True}},
                        },
                    }
                },
            },
            "sales": _ROWS_SAFE,
            "finance": _ROWS_SAFE,
        },
    ),
    "tool_with_nested_model": (
        TOOL_WITH_NESTED_MODEL,
        {
            "master": _LEG_INLINED,
            "purchase": _LEG_INLINED,
            "sales": _LEG_INLINED,
            "finance": UNCHANGED,
        },
    ),
}

PARAMS = [
    pytest.param(case, department, id=f"{case}-{department}")
    for case in EXPECTED
    for department in CONVERTERS
]


def _ordered(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


@pytest.mark.parametrize(("case", "department"), PARAMS)
def test_department_conversion_matches_the_pre_bl020_converter(case: str, department: str):
    schema, expected_by_department = EXPECTED[case]
    expected = expected_by_department[department]
    given = copy.deepcopy(schema)

    if isinstance(expected, Raises):
        with pytest.raises(expected.kind) as raised:
            CONVERTERS[department](given)
        if expected.message is not None:
            assert raised.value.args == (expected.message,)
    else:
        converted = CONVERTERS[department](given)
        want = schema if expected is UNCHANGED else expected
        assert converted == want
        assert _ordered(converted) == _ordered(want)  # 키 순서 = 요청 본문 바이트
    assert given == schema  # 입력을 바꾸지 않는다
    assert _ordered(given) == _ordered(schema)


# ── 부서 경로: 변환 결과가 그대로 요청 본문에 실린다 ─────────────────────────


class _FakeResponse:
    def __init__(self, document: dict[str, Any]) -> None:
        self._payload = json.dumps(document).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _capture(monkeypatch: pytest.MonkeyPatch, document: dict[str, Any]) -> list[dict[str, Any]]:
    bodies: list[dict[str, Any]] = []

    def fake_urlopen(request: Any, timeout: float | None = None) -> _FakeResponse:
        bodies.append(json.loads(request.data.decode("utf-8")))
        return _FakeResponse(document)

    monkeypatch.setattr("app.core.llm.providers.urllib.request.urlopen", fake_urlopen)
    return bodies


def _master_provider():
    from app.master.llm.runtime import GeminiProvider, LLMSettings

    settings = LLMSettings(
        enabled=True,
        provider="gemini",
        model="gemini-test-model",
        base_url="http://127.0.0.1:11434",
        timeout_seconds=30.0,
        max_retries=1,
        max_output_tokens=1024,
        effort=None,
    )
    return GeminiProvider(settings)


def test_master_request_keeps_length_constraints(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MASTER_GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    bodies = _capture(monkeypatch, {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
    schema = {"type": "object", "properties": {"code": {"type": "string", "minLength": 1}}}

    _master_provider().generate("system", "user", schema)

    sent = bodies[0]["generationConfig"]["responseSchema"]
    assert sent == {"type": "object", "properties": {"code": {"type": "string", "minLength": 1}}}


def test_master_unresolvable_ref_fails_before_sending(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MASTER_GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    bodies = _capture(monkeypatch, {"candidates": []})

    with pytest.raises(TypeError) as raised:
        _master_provider().generate("system", "user", copy.deepcopy(MISSING_REF))

    assert raised.value.args == ("cannot resolve schema reference: #/$defs/Missing",)
    assert bodies == []


def test_finance_tool_declaration_keeps_refs_and_defs(monkeypatch: pytest.MonkeyPatch):
    from app.finance.llm.client import gemini_tool_call

    monkeypatch.setenv("FINANCE_GEMINI_API_KEY", "test-key")
    call = {"functionCall": {"name": "record_legs", "args": {}}}
    bodies = _capture(monkeypatch, {"candidates": [{"content": {"parts": [call]}}]})

    gemini_tool_call(
        model="gemini-test-model",
        system_prompt="system",
        user_payload={},
        tool_declarations=[
            {"name": "record_legs", "parameters": copy.deepcopy(TOOL_WITH_NESTED_MODEL)}
        ],
    )

    declaration = bodies[0]["tools"][0]["function_declarations"][0]
    assert _ordered(declaration["parameters"]) == _ordered(TOOL_WITH_NESTED_MODEL)


def test_sales_request_inlines_nested_models(monkeypatch: pytest.MonkeyPatch):
    from pydantic import BaseModel

    from app.sales.llm.runtime import LLMSettings, _gemini_structured

    class Leg(BaseModel):
        amount: float

    class Plan(BaseModel):
        legs: list[Leg]

    monkeypatch.setenv("SALES_GEMINI_API_KEY", "test-key")
    reply = {"candidates": [{"content": {"parts": [{"text": '{"legs": []}'}]}}]}
    bodies = _capture(monkeypatch, reply)

    _gemini_structured(
        system_prompt="system",
        user_json="{}",
        schema_model=Plan,
        settings=LLMSettings(
            enabled=True, provider="gemini", model="gemini-test-model", timeout_seconds=30.0
        ),
    )

    sent = bodies[0]["generationConfig"]["responseSchema"]
    assert "$ref" not in json.dumps(sent)
    assert "$defs" not in json.dumps(sent)
    assert sent["properties"]["legs"]["items"]["properties"] == {
        "amount": {"title": "Amount", "type": "number"}
    }
