"""Critic-owned Ollama provider, policy, validator, retry and fallback runtime.

temperature 는 항상 0 이고 프롬프트는 생성 측과 완전히 분리된다 (설계서 §6.4).
같은 모델·같은 프롬프트를 쓰면 자기가 만든 논리를 자기가 승인한다.

프로바이더 호출과 재시도 · fallback 골격은 `app.core.llm` 이 한다 — 설정 · 재시도 ·
상태 결정 순서가 재무 · 물류 · 마스터 런타임과 같다. 여기 있는 것은 Critic 의 몫이다 —
판정 지시문(`L5_SYSTEM_PROMPT`) · Gemini 응답 스키마 · 검증기 · `CRITIC_` 설정값과 오류
문장.
"""

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import ValidationError

from app.contracts.envelope import LLMStatus
from app.core.llm.providers import (
    GEMINI_BASE_URL,
    chat_messages,
    gemini_json_request,
    gemini_request,
    gemini_text,
    ollama_chat_request,
    ollama_request,
    ollama_text,
    send_json,
)
from app.core.llm.runtime import (
    OLLAMA_BASE_URL,
    gemini_api_key,
    read_bool,
    resolve_provider_model,
    run_with_fallback,
    scoped_env,
)
from app.core.settings import load_env_file
from app.master.critic.llm.schemas import (
    InterpretationResult,
    JudgeInterpretation,
    SanitizedLLMContext,
)

# 에이전트 전용 설정 접두사 — `CRITIC_LLM_MODEL` 로 판정 모델을 생성 모델과 분리한다 (§6.4).
_ENV_PREFIX = "CRITIC_"
_NUMERIC_PATTERN = re.compile(r"\d")
_SENTENCE_SPLIT = re.compile(r"[.!?。]+")
_MAX_SUMMARY_CHARACTERS = 240
_MAX_NOTE_CHARACTERS = 400

#: Provider 별 기본 모델. stable 을 pin 한다 — `latest`·`preview` 같은 자동 갱신
#: 별칭은 출력 성향이 예고 없이 바뀌고, 그러면 판정 성적이 근거가 못 된다.
#:
#: §6.4 — 판정 모델은 생성 모델과 달라야 한다. 같은 모델·같은 논리면 자기가 만든
#:   설명을 자기가 승인한다. Gemini 에서는 다른 부서가 pin 하는 `flash-lite` 가 아니라
#:   한 단계 위인 `flash` 를 기본으로 둔다. Ollama 기본값 `gemma3:4b` 는 다른 부서의
#:   Ollama 기본값과 같으므로, Ollama 로 판정할 때 이 분리를 지키려면
#:   `CRITIC_LLM_MODEL` 로 다른 모델을 지정해야 한다.
_DEFAULT_MODELS = {
    "ollama": "gemma3:4b",
    "gemini": "gemini-3.5-flash",
}

#: Gemini `responseSchema`. JSON Schema 를 그대로 못 먹어서 직접 적는다 —
#: 판정 출력이 칸 셋뿐이라 변환기를 두는 것보다 이쪽이 읽기 쉽다 (물류와 같은 방식).
#: `JudgeInterpretation` 이 바뀌면 여기도 바꿔야 한다 — 검사가 둘을 대조한다.
_GEMINI_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
        "note": {"type": "string"},
    },
    "required": ["summary", "verdict", "note"],
}

L5_SYSTEM_PROMPT = """당신은 검증자다.
아래 결정의 설명문(rationale)이 데이터와 모순되는지만 본다.

검사 대상:
- 설명문이 인용한 근거가 facts와 binding_constraints에 실제로 있는가
- 설명문의 인과가 binding_constraints와 반대 방향은 아닌가
- signals에서 언급된 위험을 설명문이 누락했는가

검사 대상이 아닌 것:
- 수량이 적절한가 — 이미 결정론 Core가 검증했다
- 더 나은 대안이 있는가 — 당신의 역할이 아니다

규칙:
- 수량을 바꾸라고 제안하지 않는다. 문장의 문제만 지적한다.
- 숫자, 금액, 날짜, 비율을 출력하지 않는다.
- verdict는 PASS 또는 FAIL이다.
- FAIL이면 어느 문장의 어느 부분이 무엇과 모순되는지 note에 적는다.
- PASS이면 note는 짧게 근거만 적는다.
- summary는 최대 두 문장으로 작성한다.
- 모든 문장은 한국어로 작성한다.
- 지정된 JSON Schema에 맞는 JSON만 출력한다."""


@dataclass(frozen=True)
class LLMSettings:
    enabled: bool
    provider: str
    model: str
    base_url: str
    timeout_seconds: float
    max_retries: int


class LLMProvider(Protocol):
    def generate(
        self,
        context: SanitizedLLMContext,
        *,
        retry_guidance: list[str] | None = None,
    ) -> str: ...


class OllamaProvider:
    def __init__(self, settings: LLMSettings):
        self.settings = settings

    def generate(
        self,
        context: SanitizedLLMContext,
        *,
        retry_guidance: list[str] | None = None,
    ) -> str:
        payload = ollama_request(
            self.settings.model,
            chat_messages(L5_SYSTEM_PROMPT, _user_payload(context, retry_guidance)),
            response_format=JudgeInterpretation.model_json_schema(),
            # temperature 0 고정 (§6.4). 판정은 흔들리면 안 된다.
            options={"temperature": 0, "num_ctx": 4096},
        )
        document = send_json(
            ollama_chat_request(self.settings.base_url, payload),
            timeout=self.settings.timeout_seconds,
            failure_message="Critic Local LLM request failed",
        )
        return ollama_text(
            document, missing_message="Critic Local LLM response did not contain message content"
        )


class GeminiProvider:
    """Gemini REST 호출.

    API 키는 호출 시점에 환경에서 읽는다 (`CRITIC_GEMINI_API_KEY` → `GEMINI_API_KEY`).
    `LLMSettings` 에 담지 않는다 — 설정 객체는 로그·예외에 통째로 실릴 수 있다.

    자체 재시도는 없다 — 재시도는 `JudgeService` 가 소유한다.
    temperature 0 고정 (§6.4). 판정은 흔들리면 안 된다.

    `HTTPError` 는 감싸지 않는다 — 감싸면 상태 코드가 사라져 429 가 서버 다운과 같아
    보인다.

    `parts[0]` 만 읽지 않는다 — 사고 조각이 앞에 오는 모델이 있다. 앞 조각만 읽으면
    호출이 성공해도 FALLBACK 으로 떨어지고, 판정에서는 검증이 조용히 안 돈다 — 그게
    이 프로젝트에서 가장 나쁜 실패다. 사고 조각을 빼고 글자 조각을 이어붙여 읽는다
    (`core.llm.providers.gemini_text` — 부서 공통 규칙).
    """

    def __init__(self, settings: LLMSettings):
        self.settings = settings

    def generate(
        self,
        context: SanitizedLLMContext,
        *,
        retry_guidance: list[str] | None = None,
    ) -> str:
        api_key = gemini_api_key(_ENV_PREFIX)
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        payload = gemini_json_request(
            L5_SYSTEM_PROMPT, _user_payload(context, retry_guidance), _GEMINI_RESPONSE_SCHEMA
        )
        request = gemini_request(
            self.settings.model,
            payload,
            api_key=api_key,
            base_url=scoped_env(_ENV_PREFIX, "GEMINI_BASE_URL", GEMINI_BASE_URL),
        )
        document = send_json(
            request,
            timeout=self.settings.timeout_seconds,
            failure_message="Critic Gemini request failed",
            keep_http_errors=True,
        )
        text = gemini_text(document)
        if text is None:
            raise TypeError("Critic Gemini response did not contain text content")
        return text


def _user_payload(context: SanitizedLLMContext, retry_guidance: list[str] | None) -> str:
    user_payload: dict[str, object] = {"context": context.model_dump(mode="json")}
    if retry_guidance:
        user_payload["correction"] = retry_guidance
    return json.dumps(user_payload, ensure_ascii=False)


class UnavailableProvider:
    def generate(
        self,
        context: SanitizedLLMContext,
        *,
        retry_guidance: list[str] | None = None,
    ) -> str:
        del context, retry_guidance
        raise RuntimeError("Configured Critic LLM provider is not supported")


class ValidationIssue(StrEnum):
    INVALID_SCHEMA = "INVALID_SCHEMA"
    NUMERIC_OUTPUT_FORBIDDEN = "NUMERIC_OUTPUT_FORBIDDEN"
    NOTE_REQUIRED_ON_FAIL = "NOTE_REQUIRED_ON_FAIL"
    NOTE_TOO_LONG = "NOTE_TOO_LONG"
    SUMMARY_TOO_LONG = "SUMMARY_TOO_LONG"
    TOO_MANY_SENTENCES = "TOO_MANY_SENTENCES"
    REPETITIVE_OUTPUT = "REPETITIVE_OUTPUT"


class JudgeValidationError(ValueError):
    def __init__(self, issues: list[ValidationIssue]):
        super().__init__(", ".join(issues))
        self.issues = issues


class JudgeService:
    def __init__(self, settings: LLMSettings, provider: LLMProvider):
        self.settings = settings
        self.provider = provider

    def judge(
        self,
        context: SanitizedLLMContext,
        *,
        runtime_ready: bool,
        end_stage_reached: bool,
    ) -> InterpretationResult:
        """상태 결정 순서는 Finance / Logistics 런타임과 동일하다.

        DISABLED → SKIPPED_TEMPLATE → SUCCESS → FALLBACK.
        어느 경로로 끝나든 L0~L4 결정론 검증 결과는 그대로 살아 있다.

        모든 실패를 다시 묻는다 — 검증 실패는 고칠 곳을, 그 밖은 형식을 짚는다. 골격은
        `app.core.llm` 의 `run_with_fallback` 이다.
        """
        template = build_template_judgement(context)
        interpretation, status, attempts, fallback = run_with_fallback(
            enabled=self.settings.enabled,
            needs_call=needs_llm(
                context,
                runtime_ready=runtime_ready,
                end_stage_reached=end_stage_reached,
            ),
            max_retries=self.settings.max_retries,
            call=lambda guidance: self.provider.generate(context, retry_guidance=guidance),
            validate=validate_judgement,
            template=template,
            guidance_for=_next_guidance,
        )
        return self._result(interpretation, status=status, attempts=attempts, fallback=fallback)

    def _result(
        self,
        interpretation: JudgeInterpretation,
        *,
        status: LLMStatus,
        attempts: int,
        fallback: bool,
    ) -> InterpretationResult:
        return InterpretationResult(
            interpretation=interpretation,
            llm_status=status,
            llm_provider=self.settings.provider,
            llm_model=self.settings.model,
            llm_attempts=attempts,
            llm_fallback_used=fallback,
        )


#: 미지원 값은 조용히 무시하지 않고 `UnavailableProvider` 로 보내 예외를 낸다 —
#: 오타 하나로 판정이 조용히 안 도는 것이 가장 나쁘다.
_PROVIDERS: dict[str, type] = {
    "ollama": OllamaProvider,
    "gemini": GeminiProvider,
}


def get_llm_settings() -> LLMSettings:
    """에이전트 전용 설정 → 공통 설정 → 기본값 순으로 읽는다.

    `CRITIC_LLM_MODEL` 을 결정 근거를 쓰는 쪽과 다른 모델로 두는 것이 §6.4 의 요구다 —
    같은 모델·같은 논리면 자기가 만든 설명을 자기가 승인한다. 프롬프트 분리만으로는
    부족하다.

    모델은 프로바이더에 종속된 값이다 — 전역과 다른 프로바이더를 쓸 때만 전역 모델을
    건너뛴다(`resolve_provider_model` · 물류 · 마스터와 같은 규칙). 다만
    `CRITIC_LLM_MODEL` 이 직접 지정돼 있으면 그것이 이긴다 — 지정을 무시하는 것이 더
    나쁘다.

    주의: timeout · 재시도 횟수가 숫자가 아니면 예외다 — 마스터 · 물류 · 매입(기본값으로
    되돌림)과 다르다.
    """
    load_env_file()
    provider, model = resolve_provider_model(
        _ENV_PREFIX, default_provider="ollama", default_models=_DEFAULT_MODELS
    )
    return LLMSettings(
        enabled=read_bool("LLM_ENABLED", prefix=_ENV_PREFIX, default=True),
        provider=provider,
        model=model.strip(),
        base_url=scoped_env(_ENV_PREFIX, "LLM_BASE_URL", OLLAMA_BASE_URL).rstrip("/"),
        timeout_seconds=max(0.1, float(scoped_env(_ENV_PREFIX, "LLM_TIMEOUT_SECONDS", "30"))),
        max_retries=min(1, max(0, int(scoped_env(_ENV_PREFIX, "LLM_MAX_RETRIES", "1")))),
    )


def get_judge_service() -> JudgeService:
    settings = get_llm_settings()
    factory = _PROVIDERS.get(settings.provider)
    provider: LLMProvider = factory(settings) if factory else UnavailableProvider()
    return JudgeService(settings, provider)


def needs_llm(
    context: SanitizedLLMContext,
    *,
    runtime_ready: bool,
    end_stage_reached: bool,
) -> bool:
    """검증할 설명문이 실제로 있을 때만 부른다.

    앞 레이어가 FAIL 로 끊겼으면(`end_stage_reached`) L5 는 돌지 않는다 — 설계서 §8 의
    "앞 계층 FAIL 이면 뒤 레이어 생략" 규칙이다.
    """
    if not runtime_ready or end_stage_reached:
        return False
    return bool(context.rationale.strip())


def validate_judgement(raw_output: str) -> JudgeInterpretation:
    try:
        interpretation = JudgeInterpretation.model_validate_json(raw_output)
    except ValidationError as error:
        raise JudgeValidationError([ValidationIssue.INVALID_SCHEMA]) from error
    issues = _validation_issues(interpretation)
    if issues:
        raise JudgeValidationError(issues)
    return interpretation


def _validation_issues(interpretation: JudgeInterpretation) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if _NUMERIC_PATTERN.search(f"{interpretation.summary} {interpretation.note}"):
        issues.append(ValidationIssue.NUMERIC_OUTPUT_FORBIDDEN)
    if interpretation.verdict == "FAIL" and not interpretation.note.strip():
        issues.append(ValidationIssue.NOTE_REQUIRED_ON_FAIL)
    if len(interpretation.note) > _MAX_NOTE_CHARACTERS:
        issues.append(ValidationIssue.NOTE_TOO_LONG)
    if len(interpretation.summary) > _MAX_SUMMARY_CHARACTERS:
        issues.append(ValidationIssue.SUMMARY_TOO_LONG)
    sentences = [
        sentence.strip()
        for sentence in _SENTENCE_SPLIT.split(interpretation.summary)
        if sentence.strip()
    ]
    if len(sentences) > 2:
        issues.append(ValidationIssue.TOO_MANY_SENTENCES)
    normalized = [" ".join(sentence.split()) for sentence in sentences]
    if len(normalized) != len(set(normalized)):
        issues.append(ValidationIssue.REPETITIVE_OUTPUT)
    return issues


def _next_guidance(error: Exception) -> list[str]:
    """검증 실패는 고칠 곳을 짚고, 그 밖의 실패(전송 · 서버 · 형식)는 형식을 짚어 다시 묻는다."""
    if isinstance(error, JudgeValidationError):
        return retry_guidance(error.issues)
    return ["지정된 규칙과 JSON 형식에 맞춰 다시 작성하세요."]


def retry_guidance(issues: list[ValidationIssue]) -> list[str]:
    guidance = []
    if ValidationIssue.INVALID_SCHEMA in issues:
        guidance.append("지정된 세 필드만 포함한 유효한 JSON을 작성하세요.")
    if ValidationIssue.NUMERIC_OUTPUT_FORBIDDEN in issues:
        guidance.append("숫자와 날짜를 사용하지 마세요.")
    if ValidationIssue.NOTE_REQUIRED_ON_FAIL in issues:
        guidance.append("FAIL이면 어느 부분이 무엇과 모순되는지 note에 적으세요.")
    if ValidationIssue.NOTE_TOO_LONG in issues:
        guidance.append("note를 짧게 작성하세요.")
    if ValidationIssue.SUMMARY_TOO_LONG in issues or ValidationIssue.TOO_MANY_SENTENCES in issues:
        guidance.append("summary를 짧은 두 문장 이내로 작성하세요.")
    if ValidationIssue.REPETITIVE_OUTPUT in issues:
        guidance.append("같은 내용을 반복하지 마세요.")
    return guidance


def build_template_judgement(context: SanitizedLLMContext) -> JudgeInterpretation:
    """LLM 을 못 쓸 때의 기본값.

    반드시 PASS 다. 판정하지 못한 것을 FAIL 로 적으면 검증하지 않은 것을 검증했다고
    말하는 셈이 된다. 대신 '수행되지 않았다'를 note 에 남기고, 호출부가 이를
    `skipped` 로 올려 coverage 에 드러낸다 (설계서 §8).
    """
    summary = (
        " ".join(context.facts[:2])
        if context.facts
        else "결정론적 검증 결과 외에 추가로 확인된 논리 문제가 없습니다."
    )
    return JudgeInterpretation(
        summary=summary,
        verdict="PASS",
        note="L5 논리 일관성 검증이 수행되지 않았습니다.",
    )
