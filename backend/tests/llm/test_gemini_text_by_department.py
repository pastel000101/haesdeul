"""Gemini 응답 글자 읽기 — 부서 공통 규칙(`gemini_text`)이 실제 부서 호출부를 지나는지 잠근다
(2026-10-04 BL-033 사용자 결정).

```text
규칙   사고(thought) 조각 · 글자 없는 조각을 빼고, 글자 조각을 원문 그대로 순서대로 이어붙인다.
       조각별 trim · 구분자 없음. 합친 결과가 비었거나 공백뿐이면 «글자 없음».
부서   없을 때의 예외 종류 · 문장(마스터 · Critic · 물류 · 재무 · 매입 TypeError, 판매 ValueError,
       ML None, status_chat 빈 문자열)과 합친 글자의 strip(재무 · 판매)은 부서가 정한다.
```

★ 네트워크를 타지 않는다 — `urlopen` 을 `app.core.llm.providers` 에서 대역으로 바꾼다. `.env` 는
  읽지 않는다. 요청 본문(스키마)은 여기서 보지 않는다 — 읽기만 바꿨다.
"""

from __future__ import annotations

import dataclasses
import json
import types
from datetime import date
from itertools import pairwise
from typing import Any

import pytest
from pydantic import BaseModel

from app.core.llm import providers
from app.finance.llm import client as finance_client
from app.logistics.llm import runtime as logistics_runtime
from app.logistics.llm import status_chat
from app.logistics.llm.status_query import TOOL_SCHEMAS
from app.master.critic.llm import runtime as critic_runtime
from app.master.critic.llm.schemas import SanitizedLLMContext as CriticContext
from app.master.llm import runtime as master_runtime
from app.ml.llm import qa as ml_qa
from app.purchase_agent.llm import runtime as purchase_runtime
from app.sales.llm import runtime as sales_runtime

ANSWER = {"summary": "두 단어"}
ANSWER_TEXT = json.dumps(ANSWER, ensure_ascii=False)
ML_ANSWER_TEXT = json.dumps(
    {"items": ["배추"], "kinds": ["price"], "asks": [], "routes": ["price"]}, ensure_ascii=False
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setattr("app.core.settings.load_dotenv", lambda *_a, **_k: False)
    for key in (
        "GEMINI_API_KEY", "MASTER_GEMINI_API_KEY", "CRITIC_GEMINI_API_KEY",
        "FINANCE_GEMINI_API_KEY", "SALES_GEMINI_API_KEY", "LOGISTICS_GEMINI_API_KEY",
        "ML_GEMINI_API_KEY", "PURCHASE_GEMINI_API_KEY", "LLM_PROVIDER", "LLM_MODEL",
        "LLM_TIMEOUT_SECONDS", "LOGISTICS_LLM_ENABLED", "ML_LLM_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("LOGISTICS_LLM_ENABLED", "true")
    monkeypatch.setenv("ML_LLM_ENABLED", "true")
    return monkeypatch


class _Response:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self):
        return self._body


def _reply(monkeypatch: pytest.MonkeyPatch, parts: list[Any] | None) -> None:
    document = {} if parts is None else {"candidates": [{"content": {"parts": parts}}]}
    monkeypatch.setattr(
        providers.urllib.request,
        "urlopen",
        lambda *_a, **_k: _Response(json.dumps(document, ensure_ascii=False).encode()),
    )


def _split(text: str, *cuts: int) -> list[dict[str, str]]:
    bounds = [0, *cuts, len(text)]
    return [{"text": text[a:b]} for a, b in pairwise(bounds)]


# 답이 들어 있는 응답 — 모두 ANSWER 로 읽혀야 한다
READABLE: dict[str, list[Any]] = {
    "thought_text_then_answer": [{"thought": True, "text": "묻는다"}, {"text": ANSWER_TEXT}],
    "junk_parts_then_answer": [
        {"thought": True},
        {"text": ""},
        {"text": "   "},
        {"thoughtSignature": "sig"},
        {"functionCall": {"name": "x", "args": {}}},
        {"text": ANSWER_TEXT},
    ],
    # '{"summary": "두 단어"}' 를 세 조각으로 — 둘째 경계가 문자열 안 공백 바로 뒤다
    "answer_split_across_parts": _split(ANSWER_TEXT, 6, ANSWER_TEXT.index(" 단어") + 1),
    "answer_with_thought_signature": [{"text": ANSWER_TEXT, "thoughtSignature": "sig"}],
    "thought_between_answer_parts": [
        *_split(ANSWER_TEXT, 10)[:1],
        {"thought": True, "text": "중간 생각"},
        *_split(ANSWER_TEXT, 10)[1:],
    ],
}

# 글자가 없는 응답 — 부서마다 정한 실패로 간다
EMPTY: dict[str, list[Any] | None] = {
    "thought_only": [{"thought": True, "text": "생각만"}],
    "whitespace_only": [{"text": " "}, {"text": "  "}],
    "no_parts": [],
    "no_candidates": None,
}

BROKEN_JSON = '{"summary": '


# ── 부서 호출부 ──────────────────────────────────────────────────────────────


def _master(monkeypatch: pytest.MonkeyPatch) -> str:
    settings = dataclasses.replace(master_runtime.get_llm_settings(), provider="gemini", model="m")
    return master_runtime.GeminiProvider(settings).generate("s", "u", {"type": "object"})


def _critic(monkeypatch: pytest.MonkeyPatch) -> str:
    settings = dataclasses.replace(critic_runtime.get_llm_settings(), provider="gemini", model="m")
    context = CriticContext(
        cycle="A", signals=[], facts=[], binding_constraints=[], rationale="r"
    )
    return critic_runtime.GeminiProvider(settings).generate(context)


def _finance(monkeypatch: pytest.MonkeyPatch) -> str:
    return finance_client.gemini_generate(
        model="m",
        system_prompt="s",
        user_payload={},
        response_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
    )


def _purchase(monkeypatch: pytest.MonkeyPatch) -> str:
    return purchase_runtime._gemini_text(_last_document())


def _logistics(monkeypatch: pytest.MonkeyPatch) -> str:
    from app.logistics.llm.schemas import SanitizedLLMContext

    settings = logistics_runtime.get_llm_settings()
    context = SanitizedLLMContext(signals=[], facts=[], allowed_adjustments=[])
    return logistics_runtime.GeminiProvider(settings).generate(context).text


def _last_document() -> dict[str, Any]:
    """매입은 `_gemini_text(document)` 가 호출부다 — 대역 `urlopen` 의 문서를 그대로 준다."""
    with providers.urllib.request.urlopen(None) as response:
        return json.loads(response.read())


JSON_PATHS = {
    "master": _master,
    "critic": _critic,
    "finance_finalizer": _finance,
    "purchase": _purchase,
    "logistics_interpreter": _logistics,
}

FAILURE = {
    "master": (TypeError, "did not contain text content"),
    "critic": (TypeError, "Critic Gemini response did not contain text content"),
    "finance_finalizer": (TypeError, "Finance Gemini response did not contain text content"),
    "purchase": (TypeError, "contained no text part"),
    "logistics_interpreter": (TypeError, "did not contain text content"),
}


@pytest.mark.parametrize("department", JSON_PATHS)
@pytest.mark.parametrize("case", READABLE)
def test_json_paths_read_the_joined_answer(monkeypatch, department, case):
    _reply(monkeypatch, READABLE[case])
    text = JSON_PATHS[department](monkeypatch)
    assert json.loads(text) == ANSWER  # 문자열 안 공백 «두 단어» 가 경계에서 안 깨진다
    if department in {"finance_finalizer"}:
        assert text == ANSWER_TEXT  # 재무는 합친 뒤 strip
    elif case != "junk_parts_then_answer":
        assert text == ANSWER_TEXT  # 나머지는 원문 그대로(앞 공백 조각이 없으면 같다)
    else:
        assert text == "   " + ANSWER_TEXT  # 공백 조각도 원문 그대로 붙는다(trim 없음)


@pytest.mark.parametrize("department", JSON_PATHS)
@pytest.mark.parametrize("case", EMPTY)
def test_json_paths_keep_their_own_failure_when_there_is_no_text(monkeypatch, department, case):
    _reply(monkeypatch, EMPTY[case])
    kind, message = FAILURE[department]
    with pytest.raises(kind, match=message):
        JSON_PATHS[department](monkeypatch)


@pytest.mark.parametrize("department", JSON_PATHS)
def test_json_paths_hand_broken_json_to_their_validators_unchanged(monkeypatch, department):
    """깨진 JSON 은 읽기 규칙이 고치지 않는다 — 종전처럼 부서 검증이 받아 fallback 으로 간다."""
    _reply(monkeypatch, [{"thought": True, "text": "생각"}, {"text": BROKEN_JSON}])
    text = JSON_PATHS[department](monkeypatch)
    assert text == BROKEN_JSON.strip() if department == "finance_finalizer" else BROKEN_JSON
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)


def test_logistics_blank_text_is_classified_as_invalid_response(monkeypatch):
    _reply(monkeypatch, EMPTY["whitespace_only"])
    with pytest.raises(TypeError) as raised:
        _logistics(monkeypatch)
    assert logistics_runtime.classify_llm_error(raised.value) == (True, "INVALID_RESPONSE")


# ── 판매: pydantic 검증까지 ───────────────────────────────────────────────────


class _SalesOut(BaseModel):
    summary: str


def _sales(monkeypatch: pytest.MonkeyPatch) -> _SalesOut:
    monkeypatch.setenv("SALES_GEMINI_API_KEY", "test-key")
    return sales_runtime._gemini_structured(
        system_prompt="s",
        user_json="{}",
        schema_model=_SalesOut,
        settings=sales_runtime.LLMSettings(
            enabled=True, provider="gemini", model="m", timeout_seconds=30.0
        ),
    )


@pytest.mark.parametrize("case", READABLE)
def test_sales_reads_the_joined_answer(monkeypatch, case):
    _reply(monkeypatch, READABLE[case])
    assert _sales(monkeypatch).summary == "두 단어"


@pytest.mark.parametrize("case", EMPTY)
def test_sales_raises_value_error_when_there_is_no_text(monkeypatch, case):
    _reply(monkeypatch, EMPTY[case])
    with pytest.raises(ValueError, match="empty Gemini response"):
        _sales(monkeypatch)


def test_sales_broken_json_still_fails_in_validation(monkeypatch):
    from pydantic import ValidationError

    _reply(monkeypatch, [{"text": BROKEN_JSON}])
    with pytest.raises(ValidationError):
        _sales(monkeypatch)


# ── ML: 못 읽으면 None 그대로 ────────────────────────────────────────────────


def _ml(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any] | None:
    monkeypatch.setenv("ML_GEMINI_API_KEY", "test-key")
    return ml_qa.interpret("배추 가격", date(2026, 9, 17))


@pytest.mark.parametrize(
    "parts",
    [
        [{"thought": True, "text": "생각"}, {"text": ML_ANSWER_TEXT}],
        [{"thought": True}, {"text": "  "}, {"thoughtSignature": "sig"}, {"text": ML_ANSWER_TEXT}],
        _split(ML_ANSWER_TEXT, 5, 20),
    ],
    ids=["thought_then_answer", "junk_then_answer", "split"],
)
def test_ml_reads_the_joined_answer(monkeypatch, parts):
    _reply(monkeypatch, parts)
    result = _ml(monkeypatch)
    assert result is not None and result["items"] == ["배추"]


@pytest.mark.parametrize("case", [*EMPTY, "broken_json"])
def test_ml_returns_none_when_it_cannot_read(monkeypatch, case):
    _reply(monkeypatch, [{"text": BROKEN_JSON}] if case == "broken_json" else EMPTY[case])
    assert _ml(monkeypatch) is None


# ── status_chat: 표시 글자만 정리, 원본 조각은 보존 ───────────────────────────


def _chat(monkeypatch: pytest.MonkeyPatch, parts: list[Any] | None) -> status_chat.AssistantTurn:
    monkeypatch.setenv("LOGISTICS_GEMINI_API_KEY", "test-key")
    _reply(monkeypatch, parts)
    return status_chat._gemini_chat(
        types.SimpleNamespace(model="m", timeout_seconds=5),
        [{"role": "system", "content": "s"}, {"role": "user", "content": "배추 재고"}],
        list(TOOL_SCHEMAS),
    )


def test_status_chat_display_text_drops_thought_text_and_joins_verbatim(monkeypatch):
    turn = _chat(
        monkeypatch,
        [
            {"thought": True, "text": "생각"},
            {"text": "배추 "},
            {"text": "120kg "},
            {"text": "있습니다."},
        ],
    )
    assert turn.tool_calls == []
    assert turn.text == "배추 120kg 있습니다."


@pytest.mark.parametrize("case", EMPTY)
def test_status_chat_display_text_is_empty_string_when_there_is_no_text(monkeypatch, case):
    turn = _chat(monkeypatch, EMPTY[case])
    assert turn.tool_calls == [] and turn.text == ""


def test_status_chat_keeps_the_raw_tool_call_part_for_the_next_turn(monkeypatch):
    call_part = {
        "functionCall": {"name": "get_item_lots", "args": {"item_name": "배추"}},
        "thoughtSignature": "SIG-XYZ",
    }
    turn = _chat(monkeypatch, [{"thought": True, "text": "생각"}, call_part, {"text": "덧말"}])

    assert turn.text is None  # 도구를 부르는 턴에는 표시 글자가 없다(종전과 같다)
    assert [c.name for c in turn.tool_calls] == ["get_item_lots"]
    assert turn.tool_calls[0].raw == call_part  # 서명 포함 원본 그대로
    replay = status_chat._to_gemini_content(
        {"role": "assistant", "tool_calls": [dataclasses.asdict(turn.tool_calls[0])]}
    )
    assert replay["parts"] == [call_part]
