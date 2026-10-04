"""부서마다 **일부러 다른** LLM 규칙 — 2026-09-30 재구성 BL-020 에 전송을 `app/core/llm` 으로
모으면서 그대로 둔 차이를 한 표에 잠근다.

★ 새로 정한 규칙이 아니다. 옮기기 전 코드가 이렇게 돌았고(시작 사본 `738ff9ee` 와 전후 탐침으로
  대조), 공통화가 이 차이를 **조용히 하나로 합치지 않았는지**를 여기서 본다. 합칠지는 사용자
  판단이다 (설계서 «BL-020 판단이 필요한 차이»). 합치기로 하면 이 표를 함께 고친다.

```text
1. 환경변수 표     켜짐 기본값 · 빈 전용 값의 뜻 · provider 기본 · 모델 상속
                   · timeout 기본 · 잘못된 값
2. 응답 글자       2026-10-04 BL-033 결정으로 읽는 규칙은 하나(`gemini_text`)다 — 여기서는 부서가
                   정하는 나머지(없을 때의 예외 종류 · 문장, 합친 글자의 strip)만 잠근다.
                   읽기 규칙 자체는 `tests/llm/test_gemini_text_by_department.py`
3. 전송 세부       본문 인코딩 · 주소 환경변수 · 요청을 만들 때의 오류 · 전송 실패를 다시 묻나
```

네트워크 없이 잰다 — `urlopen` 은 `app.core.llm.providers` 에서 대역으로 바꾼다.
`.env` 는 읽지 않는다.
"""

from __future__ import annotations

import dataclasses
import json
import types
from typing import Any

import pytest

from app.core.llm import providers
from app.finance.llm import client as finance_client
from app.logistics.llm import runtime as logistics_runtime
from app.logistics.llm import status_chat
from app.master.critic.llm import runtime as critic_runtime
from app.master.domain.answer import AnswerFacts
from app.master.llm import answer_runtime
from app.master.llm import runtime as master_runtime
from app.ml.llm import qa as ml_qa
from app.purchase_agent.llm import runtime as purchase_runtime
from app.sales.llm import runtime as sales_runtime

_PREFIXES = ("MASTER_", "CRITIC_", "LOGISTICS_", "PURCHASE_", "SALES_", "FINANCE_", "ML_", "")
_KEYS = (
    "LLM_ENABLED", "LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL", "LLM_TIMEOUT_SECONDS",
    "LLM_MAX_RETRIES", "GEMINI_API_KEY", "GEMINI_BASE_URL", "OLLAMA_PLANNER_MODEL",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setattr("app.core.settings.load_dotenv", lambda *_a, **_k: False)
    for prefix in _PREFIXES:
        for key in _KEYS:
            monkeypatch.delenv(f"{prefix}{key}", raising=False)
    return monkeypatch


# ---------------------------------------------------------------------------
# 1. 환경변수 표
# ---------------------------------------------------------------------------


def test_enabled_defaults_differ_by_department(clean_env):
    """재무 · 마스터 · Critic · 물류 · 매입은 켬, **판매는 끔**이 기본이다."""
    assert finance_client.finance_llm_enabled() is True
    assert master_runtime.get_llm_settings().enabled is True
    assert critic_runtime.get_llm_settings().enabled is True
    assert logistics_runtime.get_llm_settings().enabled is True
    assert purchase_runtime.get_llm_settings().enabled is True
    assert sales_runtime.load_settings().enabled is False


def test_an_empty_scoped_switch_means_off_for_finance_and_sales_only(clean_env):
    """전용 값이 빈 문자열이면 — 재무 · 판매는 **꺼짐**, 나머지는 공용 값으로 넘어간다."""
    clean_env.setenv("LLM_ENABLED", "true")
    for prefix in ("FINANCE_", "SALES_", "MASTER_", "CRITIC_", "LOGISTICS_", "PURCHASE_"):
        clean_env.setenv(f"{prefix}LLM_ENABLED", "")
    assert finance_client.finance_llm_enabled() is False
    assert sales_runtime.load_settings().enabled is False
    assert master_runtime.get_llm_settings().enabled is True
    assert critic_runtime.get_llm_settings().enabled is True
    assert logistics_runtime.get_llm_settings().enabled is True
    assert purchase_runtime.get_llm_settings().enabled is True


def test_provider_defaults_and_global_inheritance(clean_env):
    assert master_runtime.get_llm_settings().provider == "anthropic"
    assert purchase_runtime.get_llm_settings().provider == "anthropic"
    assert critic_runtime.get_llm_settings().provider == "ollama"
    assert logistics_runtime.get_llm_settings().provider == "ollama"
    assert finance_client.finance_provider_name() == "gemini"
    assert sales_runtime.load_settings().provider == "gemini"

    clean_env.setenv("LLM_PROVIDER", "ollama")
    assert master_runtime.get_llm_settings().provider == "ollama"
    # 재무 · 판매는 전역 provider 를 상속하지 않는다.
    assert finance_client.finance_provider_name() == "gemini"
    assert sales_runtime.load_settings().provider == "gemini"


def test_a_scoped_provider_skips_the_global_model_except_in_purchase(clean_env):
    """전역 `LLM_MODEL` 은 전역 provider 의 모델이다 — 부서만 Gemini 로 가면 물려받지 않는다.
    매입만 그대로 물려받는다(옮기기 전 그대로)."""
    clean_env.setenv("LLM_PROVIDER", "ollama")
    clean_env.setenv("LLM_MODEL", "gemma3:4b")
    for prefix in ("MASTER_", "CRITIC_", "LOGISTICS_", "PURCHASE_", "SALES_", "FINANCE_"):
        clean_env.setenv(f"{prefix}LLM_PROVIDER", "gemini")
    assert master_runtime.get_llm_settings().model == "gemini-3.5-flash-lite"
    assert critic_runtime.get_llm_settings().model == "gemini-3.5-flash"
    assert logistics_runtime.get_llm_settings().model == "gemini-3.5-flash-lite"
    assert sales_runtime.load_settings().model == "gemini-3.5-flash-lite"
    assert finance_client.finance_model("gemini") == "gemini-3.5-flash-lite"
    assert purchase_runtime.get_llm_settings().model == "gemma3:4b"


def test_timeout_defaults_and_bad_values(clean_env):
    assert master_runtime.get_llm_settings().timeout_seconds == 30.0
    assert logistics_runtime.get_llm_settings().timeout_seconds == 10.0
    assert critic_runtime.get_llm_settings().timeout_seconds == 30.0
    assert finance_client.llm_timeout_seconds() == 30.0

    clean_env.setenv("LLM_TIMEOUT_SECONDS", "abc")
    # 마스터 · 물류 · 매입은 기본값으로 되돌리고, Critic · 재무 · 판매는 예외를 낸다.
    assert master_runtime.get_llm_settings().timeout_seconds == 30.0
    assert logistics_runtime.get_llm_settings().timeout_seconds == 10.0
    assert purchase_runtime.get_llm_settings().timeout_seconds == 30.0
    with pytest.raises(ValueError):
        critic_runtime.get_llm_settings()
    with pytest.raises(ValueError):
        finance_client.llm_timeout_seconds()
    with pytest.raises(ValueError):
        sales_runtime.load_settings()


def test_retries_are_clamped_to_one(clean_env):
    clean_env.setenv("LLM_MAX_RETRIES", "5")
    for settings in (
        master_runtime.get_llm_settings(),
        critic_runtime.get_llm_settings(),
        logistics_runtime.get_llm_settings(),
        purchase_runtime.get_llm_settings(),
    ):
        assert settings.max_retries == 1


def test_ml_switch_and_key(clean_env):
    clean_env.setenv("ML_GEMINI_API_KEY", " k ")
    assert ml_qa.enabled() is True
    clean_env.setenv("ML_LLM_ENABLED", "FALSE")
    assert ml_qa.enabled() is True, "ML 은 0 · false · False 만 끈다"
    clean_env.setenv("ML_LLM_ENABLED", "false")
    assert ml_qa.enabled() is False


# ---------------------------------------------------------------------------
# 2. Gemini 응답 글자 — 읽기 규칙은 하나, 없을 때의 처리와 strip 은 부서가 정한다
# ---------------------------------------------------------------------------

_THOUGHT_FIRST = {
    "candidates": [{"content": {"parts": [{"thought": True, "text": "사고"}, {"text": " 답 "}]}}]
}
_BLANK_FIRST = {"candidates": [{"content": {"parts": [{"text": "   "}, {"text": "답"}]}}]}
_BLANK_ONLY = {"candidates": [{"content": {"parts": [{"text": " "}]}}]}


def _gemini_reply(clean_env, document: dict[str, Any]) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def fake(request, timeout=None):
        seen.append({"url": request.full_url, "data": request.data, "timeout": timeout})
        return _Response(json.dumps(document).encode())

    clean_env.setattr(providers.urllib.request, "urlopen", fake)
    return seen


class _Response:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self):
        return self._body


def test_master_keeps_the_joined_text_as_is_and_fails_on_blank(clean_env):
    """마스터는 합친 글자를 그대로 넘긴다(strip 없음). 글자가 없으면 `TypeError`."""
    clean_env.setenv("GEMINI_API_KEY", "k")
    settings = dataclasses.replace(master_runtime.get_llm_settings(), provider="gemini", model="m")
    _gemini_reply(clean_env, _THOUGHT_FIRST)
    assert master_runtime.GeminiProvider(settings).generate("s", "u", {}) == " 답 "
    _gemini_reply(clean_env, _BLANK_FIRST)
    assert master_runtime.GeminiProvider(settings).generate("s", "u", {}) == "   답"
    _gemini_reply(clean_env, _BLANK_ONLY)
    with pytest.raises(TypeError, match="did not contain text content"):
        master_runtime.GeminiProvider(settings).generate("s", "u", {})


def test_finance_strips_the_joined_text_and_fails_on_blank(clean_env):
    assert finance_client._gemini_response_text(_THOUGHT_FIRST) == "답"
    assert finance_client._gemini_response_text(_BLANK_FIRST) == "답"
    with pytest.raises(TypeError, match="Finance Gemini response did not contain text content"):
        finance_client._gemini_response_text(_BLANK_ONLY)


def test_purchase_keeps_the_joined_text_as_is_and_fails_on_blank(clean_env):
    assert purchase_runtime._gemini_text(_THOUGHT_FIRST) == " 답 "
    assert purchase_runtime._gemini_text(_BLANK_FIRST) == "   답"
    with pytest.raises(TypeError, match="contained no text part"):
        purchase_runtime._gemini_text(_BLANK_ONLY)


def test_sales_strips_the_joined_text_and_raises_value_error_on_blank(clean_env):
    assert sales_runtime._gemini_response_text(_THOUGHT_FIRST) == "답"
    with pytest.raises(ValueError, match="empty Gemini response"):
        sales_runtime._gemini_response_text(_BLANK_ONLY)


def test_logistics_keeps_the_joined_text_as_is_and_raises_type_error_on_blank(clean_env):
    """물류는 `TypeError` 로 올린다 — `classify_llm_error` 가 INVALID_RESPONSE 로 분류한다."""
    clean_env.setenv("LOGISTICS_GEMINI_API_KEY", "k")
    _gemini_reply(clean_env, _THOUGHT_FIRST)
    settings = logistics_runtime.get_llm_settings()
    result = logistics_runtime.GeminiProvider(settings).generate(_logistics_context())
    assert result.text == " 답 "
    _gemini_reply(clean_env, _BLANK_ONLY)
    with pytest.raises(TypeError, match="did not contain text content") as raised:
        logistics_runtime.GeminiProvider(settings).generate(_logistics_context())
    assert logistics_runtime.classify_llm_error(raised.value) == (True, "INVALID_RESPONSE")


def _logistics_context():
    from app.logistics.llm.schemas import SanitizedLLMContext

    return SanitizedLLMContext(signals=[], facts=[], allowed_adjustments=[])


# ---------------------------------------------------------------------------
# 3. 전송 세부
# ---------------------------------------------------------------------------


def test_status_chat_sends_raw_korean(clean_env):
    """status_chat 만 `ensure_ascii=False` 로 보낸다 — 한글이 바이트 그대로 간다."""
    clean_env.setenv("LOGISTICS_GEMINI_API_KEY", "k")
    seen = _gemini_reply(clean_env, {"candidates": [{"content": {"parts": [{"text": "답"}]}}]})
    settings = types.SimpleNamespace(model="m", timeout_seconds=5)
    status_chat._gemini_chat(settings, [{"role": "user", "content": "배추 재고"}], [])
    assert "배추".encode() in seen[0]["data"]


def test_logistics_gemini_ignores_the_shared_base_url(clean_env):
    clean_env.setenv("LOGISTICS_GEMINI_API_KEY", "k")
    clean_env.setenv("GEMINI_BASE_URL", "https://shared.example")
    seen = _gemini_reply(clean_env, _THOUGHT_FIRST)
    logistics_runtime.GeminiProvider(logistics_runtime.get_llm_settings()).generate(
        _logistics_context()
    )
    assert seen[0]["url"].startswith(providers.GEMINI_BASE_URL)

    clean_env.setenv("MASTER_LLM_PROVIDER", "gemini")
    clean_env.setenv("MASTER_GEMINI_API_KEY", "k")
    master_runtime.GeminiProvider(master_runtime.get_llm_settings()).generate("s", "u", {})
    assert seen[1]["url"].startswith("https://shared.example/")


def test_ml_raises_on_a_bad_address_instead_of_returning_none(clean_env):
    """ML 은 **보낼 때**만 예외를 삼키고 `None` 을 낸다 — 요청을 만들 때의 오류는 올라간다."""
    clean_env.setenv("ML_GEMINI_API_KEY", "k")
    clean_env.setenv("ML_GEMINI_BASE_URL", "nohost")
    with pytest.raises(ValueError, match="unknown url type"):
        ml_qa.interpret("내일 배추", __import__("datetime").date(2026, 9, 30))


def test_finance_ollama_address_reads_only_the_shared_key(clean_env):
    clean_env.setenv("FINANCE_LLM_BASE_URL", "http://finance:1")
    clean_env.setenv("LLM_BASE_URL", "http://shared:2/")
    assert finance_client.ollama_base_url() == "http://shared:2"


def test_master_narrative_does_not_retry_a_transport_failure(clean_env):
    calls = []

    class Down:
        def generate(self, system, user, schema):
            calls.append(user)
            raise RuntimeError("down")

    settings = master_runtime.get_llm_settings()
    result = answer_runtime.NarrativeService(settings, Down()).write(
        AnswerFacts(headline="확인", answered=("물류",))
    )
    assert (result.llm_status, result.llm_attempts, len(calls)) == ("FALLBACK", 1, 1)
