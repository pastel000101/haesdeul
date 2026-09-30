"""Finance Finalizer — **검증된 Evidence 에서 설명 키를 고른다.**

문장을 쓰지 않는다. `_FINAL_EXPLANATIONS` 의 키 중 하나를 고를 뿐이라, 새 숫자나
새 주장이 설명을 통해 들어올 자리가 없다.

★ 사용자에게 나가는 문장 자체는 `app.finance.user_messages` 소유다. 여기서는 **어느 문장을
  고를지**만 정한다 — 문장을 여기 두면 Provider 코드마다 조금씩 다른 말투가 생긴다.

★ 요청을 보내는 줄은 `app.core.llm` 이다 (2026-09-30 재구성 BL-020). 재시도하지 않는다 —
  Gemini 가 못 받으면 `planner` 의 가용성 대체가 Ollama Finalizer 로 한 번 옮긴다.
"""

from __future__ import annotations

import json

from app.contracts.core import Evidence
from app.core.llm.providers import chat_messages, ollama_chat_request, ollama_request, send_json
from app.finance.domain.messages import FINANCE_EXPLANATIONS, explanation_keys
from app.finance.llm.client import (
    finance_model,
    gemini_generate,
    llm_timeout_seconds,
    ollama_base_url,
)
from app.finance.schemas.agent import FinanceMode

#: 사용자에게 그대로 보이는 확정 설명. **정본은 `app.finance.user_messages`** 다.
#:
#: ★ **키는 기계 계약이고 값만 표시 문장이다.** Finalizer 는 이 키 중 하나를 고를 뿐이라,
#:   설명을 어떻게 고쳐 써도 LLM 이 숫자를 새로 만들 자리는 여전히 없다.
_FINAL_EXPLANATIONS = FINANCE_EXPLANATIONS

#: Finalizer 에게 주는 규율. **사용자가 읽을 문장을 고르는 일**이라는 것을 명시한다.
#:
#: ★ 내부 구조를 말하지 말라고 적어 두는 이유: 모델은 프롬프트에 들어간 관측을 그대로
#:   흉내 내려는 경향이 있다. 고정 문장을 고르는 구조가 1차 방어이고, 이 규율은 그 위의
#:   2차 방어다 — 둘 중 하나만 두지 않는다.
_FINALIZER_SYSTEM_PROMPT = (
    "You choose the Korean explanation that a business user will read for a Finance "
    "review that is already complete. Answer only by selecting one allowed "
    "explanation key.\n"
    "Rules:\n"
    "- The reply the user sees is Korean and written for a finance/business reader.\n"
    "- Explain what the result means for their purchase decision and why.\n"
    "- Use only the verified evidence you are given.\n"
    "- Never calculate, derive, restate or invent any number or policy value.\n"
    "- Never change the verdict; it is already decided by deterministic rules.\n"
    "- Never mention internal architecture, agent framework, LangChain, Harness, "
    "Planner, Registry, Capability, Dependency, Tool names, run state or any other "
    "debugging detail.\n"
    "- Do not translate English implementation terms literally; the user does not "
    "know them."
)


class OllamaFinanceFinalizer:
    """조사 Planner와 분리된 Evidence 전용 LLM finalization.

    ★ 주소 · timeout 은 **만들 때** 읽는다(부를 때마다 다시 읽지 않는다 — 옮기기 전 그대로).
      전송 예외를 감싸지 않는다(가용성 판별이 본다).
    """

    def __init__(self, *, model: str | None = None) -> None:
        self.model = model or finance_model("ollama")
        self.base_url = ollama_base_url()
        self.timeout = llm_timeout_seconds()
        self.attempts = 0

    def finalize(
        self,
        *,
        mode: FinanceMode,
        business_status: str,
        evidences: tuple[Evidence, ...],
        has_verified_adjustment: bool = False,
    ) -> str:
        self.attempts += 1
        allowed = explanation_keys(
            mode, business_status, has_verified_adjustment=has_verified_adjustment
        )
        body = ollama_request(
            self.model,
            chat_messages(
                _FINALIZER_SYSTEM_PROMPT,
                json.dumps(
                    {
                        "mode": mode,
                        "business_status": business_status,
                        "verified_claims": [item.claim for item in evidences],
                        "allowed_explanation_keys": allowed,
                    }
                ),
            ),
            response_format={
                "type": "object",
                "properties": {"explanation_key": {"type": "string", "enum": allowed}},
                "required": ["explanation_key"],
                "additionalProperties": False,
            },
            options={"temperature": 0},
        )
        raw = send_json(ollama_chat_request(self.base_url, body), timeout=self.timeout)
        selected = json.loads(raw["message"]["content"])["explanation_key"]
        if selected not in allowed:
            raise ValueError("Finance finalization selected an unsupported explanation")
        return _FINAL_EXPLANATIONS[selected]


class GeminiFinanceFinalizer:
    """검증된 Evidence에서 설명 키만 고르는 Gemini Finalizer."""

    def __init__(self) -> None:
        self.model = finance_model("gemini")
        self.attempts = 0

    def finalize(
        self,
        *,
        mode: FinanceMode,
        business_status: str,
        evidences: tuple[Evidence, ...],
        has_verified_adjustment: bool = False,
    ) -> str:
        self.attempts += 1
        allowed = explanation_keys(
            mode, business_status, has_verified_adjustment=has_verified_adjustment
        )
        selected = json.loads(
            gemini_generate(
                model=self.model,
                system_prompt=_FINALIZER_SYSTEM_PROMPT,
                user_payload={
                    "mode": mode,
                    "business_status": business_status,
                    "verified_claims": [item.claim for item in evidences],
                    "allowed_explanation_keys": allowed,
                },
                response_schema={
                    "type": "object",
                    "properties": {
                        "explanation_key": {"type": "string", "enum": allowed}
                    },
                    "required": ["explanation_key"],
                },
            )
        )["explanation_key"]
        if selected not in allowed:
            raise ValueError("Finance finalization selected an unsupported explanation")
        return _FINAL_EXPLANATIONS[selected]


class DeterministicFinanceFinalizer:
    """동일한 검증 완료 설명 계약을 구현하는 테스트/오프라인 finalizer."""

    model = "deterministic-finance-finalizer"

    def __init__(self) -> None:
        self.attempts = 0

    def finalize(
        self,
        *,
        mode: FinanceMode,
        business_status: str,
        evidences: tuple[Evidence, ...],
        has_verified_adjustment: bool = False,
    ) -> str:
        self.attempts += 1
        del evidences
        return _FINAL_EXPLANATIONS[
            explanation_keys(
                mode, business_status, has_verified_adjustment=has_verified_adjustment
            )[0]
        ]
