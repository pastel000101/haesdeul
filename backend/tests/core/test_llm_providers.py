"""`app/core/llm/providers.py` — 외부 LLM 호출의 한 자리 (2026-09-30 재구성 BL-020).

네트워크 없이 잰다. `urlopen` · SDK 클래스를 대역으로 바꿔 **넘긴 것**(주소 · 머리글 · 본문 바이트 ·
timeout · SDK 인자)과 **돌려준 것**(값 · 예외 · 원인)을 본다. SDK 안에서 무슨 일이 나는지는
재지 않는다.

부서별 동작이 옮기기 전과 같은지는 부서 검사와 전후 탐침이 본다 — 여기서 잠그는 것은 core 가
약속한 규칙이다: 오류를 어떻게 감싸나 · 재시도하지 않는다 · 응답 글자를 어떻게 고르나 ·
요청 본문의 칸 순서 · Gemini 스키마 변환 두 벌의 규칙.
"""

from __future__ import annotations

import io
import json
import sys
import types
import urllib.error
import urllib.request
from typing import Any

import pytest

from app.core.llm import providers
from app.core.llm.providers import (
    GEMINI_BASE_URL,
    gemini_json_request,
    gemini_parts,
    gemini_request,
    gemini_response_schema,
    gemini_safe_schema,
    gemini_strict_schema,
    gemini_text,
    gemini_tool_request,
    json_request,
    ollama_chat_request,
    ollama_request,
    ollama_text,
    send_json,
)


class _Response:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self):
        return self._body


def _urlopen(monkeypatch: pytest.MonkeyPatch, *outcomes: Any) -> list[dict[str, Any]]:
    """`urlopen` 대역 — 부를 때마다 다음 결과(바이트면 응답, 예외면 던진다)를 낸다."""
    calls: list[dict[str, Any]] = []
    queue = list(outcomes)

    def fake(request, timeout=None):
        calls.append({"request": request, "timeout": timeout})
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return _Response(outcome)

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake)
    return calls


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "status", {}, io.BytesIO(b"{}"))


# ── 보내기 · 오류 감싸기 ─────────────────────────────────────────────────────


def test_send_json_reads_the_body_once_and_does_not_retry(monkeypatch):
    calls = _urlopen(monkeypatch, b'{"ok": true}')
    request = json_request("http://h/x", {"a": 1})

    assert send_json(request, timeout=3.5) == {"ok": True}
    assert len(calls) == 1
    assert calls[0]["timeout"] == 3.5


@pytest.mark.parametrize(
    "error",
    [_http_error(500), urllib.error.URLError("refused"), TimeoutError("slow")],
)
def test_without_a_failure_message_errors_pass_through_unwrapped(monkeypatch, error):
    _urlopen(monkeypatch, error)
    with pytest.raises(type(error)) as raised:
        send_json(json_request("http://h/x", {}), timeout=1)
    assert raised.value is error


def test_broken_json_passes_through_without_a_failure_message(monkeypatch):
    _urlopen(monkeypatch, b"<html>")
    with pytest.raises(json.JSONDecodeError):
        send_json(json_request("http://h/x", {}), timeout=1)


@pytest.mark.parametrize(
    "error",
    [_http_error(500), urllib.error.URLError("refused"), TimeoutError("slow"), b"<html>"],
)
def test_ollama_style_wraps_transport_failures_including_http_errors(monkeypatch, error):
    _urlopen(monkeypatch, error)
    with pytest.raises(RuntimeError, match="^Dept Local LLM request failed$") as raised:
        send_json(
            json_request("http://h/x", {}),
            timeout=1,
            failure_message="Dept Local LLM request failed",
        )
    cause = raised.value.__cause__
    assert cause is not None
    if isinstance(error, BaseException):
        assert cause is error
    else:
        assert isinstance(cause, json.JSONDecodeError)


def test_gemini_style_keeps_the_http_status_and_wraps_the_rest(monkeypatch):
    error = _http_error(429)
    _urlopen(monkeypatch, error, TimeoutError("slow"))
    request = json_request("http://h/x", {})

    with pytest.raises(urllib.error.HTTPError) as raised:
        send_json(
            request, timeout=1, failure_message="Dept Gemini request failed", keep_http_errors=True
        )
    assert raised.value is error and raised.value.code == 429

    with pytest.raises(RuntimeError, match="^Dept Gemini request failed$"):
        send_json(
            request, timeout=1, failure_message="Dept Gemini request failed", keep_http_errors=True
        )


def test_other_os_errors_are_never_wrapped(monkeypatch):
    """감싸는 것은 시간 초과 · 연결 실패 · 깨진 JSON 뿐이다 — 부서 except 그대로."""
    error = ConnectionResetError("reset")
    _urlopen(monkeypatch, error)
    with pytest.raises(ConnectionResetError):
        send_json(json_request("http://h/x", {}), timeout=1, failure_message="x")


def test_a_bad_address_fails_when_the_request_is_built_not_when_it_is_sent():
    """ML 은 요청을 만든 뒤에만 예외를 삼킨다 — 주소가 틀리면 만들 때 `ValueError` 가 나야 한다."""
    with pytest.raises(ValueError, match="unknown url type"):
        gemini_request("m", {}, api_key="k", base_url="nohost")


# ── 요청 모양 ───────────────────────────────────────────────────────────────


def test_json_request_headers_and_encoding():
    request = json_request("http://h/x", {"k": "한글"}, headers={"x-goog-api-key": "K"})
    assert request.get_method() == "POST"
    assert request.header_items() == [("Content-type", "application/json"), ("X-goog-api-key", "K")]
    assert request.data == json.dumps({"k": "한글"}).encode("utf-8")
    assert request.data.isascii()

    raw = json_request(
        "http://h/x", {"k": "한글", "d": object()}, ensure_ascii=False, json_default=str
    )
    assert "한글".encode() in raw.data


def test_gemini_request_puts_the_key_in_a_header_and_trims_the_base():
    request = gemini_request("gemini-x", {}, api_key="SECRET", base_url="https://g.example//")
    assert request.full_url == "https://g.example/models/gemini-x:generateContent"
    assert "SECRET" not in request.full_url
    assert dict(request.header_items())["X-goog-api-key"] == "SECRET"
    assert gemini_request("m", {}, api_key="k").full_url.startswith(GEMINI_BASE_URL + "/models/")


def test_ollama_chat_request_address():
    assert ollama_chat_request("http://o:1", {}).full_url == "http://o:1/api/chat"


def test_request_bodies_keep_their_key_order():
    """본문 바이트가 옮기기 전과 같으려면 칸 순서도 같아야 한다."""
    structured = ollama_request(
        "m", [], options={"temperature": 0}, response_format={"type": "object"}
    )
    assert list(structured) == ["model", "stream", "think", "format", "messages", "options"]
    tools = ollama_request("m", [], options={"temperature": 0}, tools=[])
    assert list(tools) == ["model", "stream", "think", "tools", "messages", "options"]

    body = gemini_json_request("sys", "user", {"type": "object"}, max_output_tokens=64)
    assert list(body) == ["system_instruction", "contents", "generationConfig"]
    assert list(body["generationConfig"]) == [
        "temperature",
        "responseMimeType",
        "responseSchema",
        "maxOutputTokens",
    ]
    assert "maxOutputTokens" not in gemini_json_request("s", "u", {})["generationConfig"]

    call = gemini_tool_request("sys", [], [{"name": "f"}], function_calling={"mode": "AUTO"})
    assert list(call) == [
        "system_instruction",
        "contents",
        "tools",
        "toolConfig",
        "generationConfig",
    ]
    assert call["toolConfig"] == {"functionCallingConfig": {"mode": "AUTO"}}


# ── 응답 읽기 ───────────────────────────────────────────────────────────────


def test_ollama_text_requires_a_string():
    assert ollama_text({"message": {"content": "x"}}, missing_message="m") == "x"
    with pytest.raises(TypeError, match="^missing$"):
        ollama_text({"message": {}}, missing_message="missing")
    with pytest.raises(TypeError):
        ollama_text({}, missing_message="missing")


def test_gemini_parts_tolerates_missing_candidates():
    assert gemini_parts({}) == []
    assert gemini_parts({"candidates": [{}]}) == []
    assert gemini_parts({"candidates": [{"content": {"parts": [{"text": "a"}]}}]}) == [
        {"text": "a"}
    ]


_THOUGHT = {"thought": True, "text": "thinking"}


def _doc(parts: list[Any]) -> dict[str, Any]:
    return {"candidates": [{"content": {"parts": parts}}]}


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        ([_THOUGHT, {"text": "답"}], "답"),  # 글자가 있는 사고 조각도 답이 아니다
        ([{"thought": True}, {"text": "답"}], "답"),
        ([{"thoughtSignature": "sig"}, {"text": "답"}], "답"),  # text 없는 조각
        ([{"text": "답", "thoughtSignature": "sig"}], "답"),  # 서명 붙은 답은 사고 조각이 아니다
        ([{"text": "   "}, {"text": "답"}], "   답"),  # 조각별 trim 없음
        ([{"text": ""}, {"text": "답"}], "답"),
        ([{"text": '{"a":'}, _THOUGHT, {"text": " 1}"}], '{"a": 1}'),  # 순서대로 · 구분자 없음
        ([{"text": '{"s": "두 '}, {"text": '단어"}'}], '{"s": "두 단어"}'),  # 경계의 공백 보존
        (["junk", {"text": 5}, {"text": "답"}], "답"),  # dict 아닌 조각 · 문자열 아닌 text
        ([_THOUGHT], None),
        ([{"text": "   "}, {"text": ""}], None),  # 합친 결과가 공백뿐
        ([{"functionCall": {}}], None),
        ([], None),
    ],
)
def test_gemini_text_joins_non_thought_text_parts_verbatim(parts, expected):
    assert gemini_text(_doc(parts)) == expected


def test_gemini_text_tolerates_missing_candidates():
    assert gemini_text({}) is None
    assert gemini_text({"candidates": [{}]}) is None


# ── SDK: 재시도를 끄고, 키 → 모델 순서로 확인한다 ────────────────────────────


def _fake_sdk(monkeypatch, name: str, reply: Any) -> list[tuple[str, dict[str, Any]]]:
    seen: list[tuple[str, dict[str, Any]]] = []

    class Client:
        def __init__(self, **kwargs):
            seen.append(("init", kwargs))
            self.messages = self
            self.chat = self
            self.completions = self

        def create(self, **kwargs):
            seen.append(("create", kwargs))
            return reply

    module = types.ModuleType(name)
    setattr(module, "Anthropic" if name == "anthropic" else "OpenAI", Client)
    monkeypatch.setitem(sys.modules, name, module)
    return seen


def test_anthropic_turns_sdk_retries_off_and_reads_the_first_text_block(monkeypatch):
    blocks = [
        types.SimpleNamespace(type="thinking", text=""),
        types.SimpleNamespace(type="text", text="{}"),
    ]
    seen = _fake_sdk(monkeypatch, "anthropic", types.SimpleNamespace(content=blocks))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "K")

    text = providers.anthropic_json(
        provider="anthropic",
        model="claude-x",
        system="s",
        user="u",
        schema={"type": "object"},
        max_tokens=256,
        timeout=9.0,
        effort=None,
    )

    assert text == "{}"
    assert seen[0] == ("init", {"api_key": "K", "timeout": 9.0, "max_retries": 0})
    assert seen[1][1]["output_config"] == {
        "format": {"type": "json_schema", "schema": {"type": "object"}}
    }
    assert seen[1][1]["max_tokens"] == 256


def test_anthropic_checks_the_key_before_the_model(monkeypatch):
    _fake_sdk(monkeypatch, "anthropic", None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="^ANTHROPIC_API_KEY is not set$"):
        providers.anthropic_json(
            provider="anthropic",
            model="",
            system="s",
            user="u",
            schema={},
            max_tokens=1,
            timeout=1,
            effort=None,
        )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "K")
    with pytest.raises(RuntimeError, match="^LLM_MODEL is not set for provider 'anthropic'$"):
        providers.anthropic_json(
            provider="anthropic",
            model="",
            system="s",
            user="u",
            schema={},
            max_tokens=1,
            timeout=1,
            effort=None,
        )


def test_openai_turns_sdk_retries_off_and_sends_a_strict_schema(monkeypatch):
    message = types.SimpleNamespace(content='{"a": 1}')
    reply = types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])
    seen = _fake_sdk(monkeypatch, "openai", reply)
    monkeypatch.setenv("OPENAI_API_KEY", "K")

    text = providers.openai_json(
        provider="openai",
        model="gpt-x",
        system="s",
        user="u",
        schema={"type": "object"},
        schema_name="intent",
        max_tokens=100,
        timeout=5.0,
    )

    assert text == '{"a": 1}'
    assert seen[0] == ("init", {"api_key": "K", "timeout": 5.0, "max_retries": 0})
    create = seen[1][1]
    assert create["max_completion_tokens"] == 100
    assert create["response_format"]["json_schema"] == {
        "name": "intent",
        "strict": True,
        "schema": {"type": "object"},
    }


# ── Gemini 스키마 변환 두 벌 ─────────────────────────────────────────────────


def test_response_schema_drops_unsupported_keys_keeps_description_and_inlines_refs():
    schema = {
        "title": "T",
        "description": "D",
        "additionalProperties": False,
        "$defs": {"Leaf": {"title": "Leaf", "type": "string", "minLength": 1}},
        "properties": {
            "leaf": {"$ref": "#/$defs/Leaf", "description": "L"},
            "maybe": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None},
        },
    }
    assert gemini_response_schema(schema) == {
        "description": "D",
        "properties": {
            "leaf": {"type": "string", "description": "L"},
            "maybe": {"type": "integer", "nullable": True},
        },
    }


def test_response_schema_keeps_unions_and_strict_schema_rejects_them():
    union = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    assert gemini_response_schema(union) == {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    with pytest.raises(TypeError, match="anyOf"):
        gemini_strict_schema(union)
    with pytest.raises(TypeError, match="anyOf"):
        gemini_strict_schema({"anyOf": [{"type": "null"}]})
    assert gemini_strict_schema({"anyOf": [{"type": "string"}, {"type": "null"}]}) == {
        "type": "string",
        "nullable": True,
    }


def test_response_schema_refuses_an_unresolvable_reference():
    with pytest.raises(KeyError):
        gemini_response_schema({"$ref": "#/$defs/Missing"})


def test_safe_schema_lowers_representation_only():
    schema = {
        "title": "Args",
        "additionalProperties": False,
        "$defs": {"Pos": {"type": "string", "enum": ["A"]}},
        "properties": {
            "axis": {"const": "amount", "default": "amount", "type": "string"},
            "pos": {"$ref": "#/$defs/Pos"},
            "maybe": {"anyOf": [{"type": "number"}, {"type": "null"}], "title": "Maybe"},
        },
    }
    assert gemini_safe_schema(schema) == {
        "title": "Args",
        "properties": {
            "axis": {"default": "amount", "type": "string", "enum": ["amount"]},
            "pos": {"type": "string", "enum": ["A"]},
            "maybe": {"title": "Maybe", "nullable": True, "type": "number"},
        },
    }
    with pytest.raises(TypeError, match="cannot resolve schema reference"):
        gemini_safe_schema({"type": "object", "properties": {"x": {"$ref": "#/$defs/Missing"}}})
