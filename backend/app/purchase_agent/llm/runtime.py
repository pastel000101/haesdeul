"""프로바이더 · 검증 · 재시도 · fallback (팀 규약 준수 — finance/critic 런타임과 같은 배치).

**노드는 이 모듈을 직접 부르지 않는다** — ``mix.make_mix_selector()``만 안다.

프로바이더는 4종이고 ``LLM_PROVIDER``로 고른다. **팀의 환경변수 규약이 이미 프로바이더
중립**이라 이름을 새로 만들지 않았다:

* ``anthropic`` — Messages API + 구조화 출력(``output_config.format``)
* ``openai``    — Chat Completions + ``response_format`` json_schema(strict)
* ``ollama``    — 팀 기존 4벌과 같은 로컬 경로 (``format``에 JSON Schema)
* ``gemini``    — REST + ``responseSchema`` (표준 라이브러리만 · 🔴 팀 다섯 파트가 쓰는 것)

🔴 **``gemini``가 늦게 들어온 이유를 적어 둔다.** 마스터·critic·판매·재무·물류가 전부
gemini를 쓰는데 매입만 표에 그 이름이 없어서, ``.env``에 ``GEMINI_API_KEY``가 있어도
**매입만 그 키를 못 썼다.** 조립이 표(``PROVIDERS``)를 보고 도는 구조라 "키가 없다"가
아니라 "이름이 없다"가 막고 있었고, 그 둘은 증상이 같다 — 둘 다 fallback이다.

**검증 체인은 프로바이더 밖에 있다.** 프로바이더는 "문자열을 받아온다"까지만 하고,
후보 대조·숫자 금지·재시도는 ``MixSelectionService``가 소유한다 — 프로바이더를 갈아끼워도
판정이 갈라지지 않는다. JSON 강제 방식만 프로바이더마다 다르다(구조화 출력 API가 서로 다르다).

**API 키는 ``.env``에서만 읽는다.** 코드에 기본값을 두지 않고, ``LLMSettings``에도 싣지
않는다 — 설정 객체는 로그·예외에 실릴 수 있고 키가 거기 묻어나가면 안 된다. 각 프로바이더가
호출 직전에 환경에서 직접 읽는다. 키가 없으면 예외를 던지고 **fallback으로 간다** —
팀원이 브랜치만 받아도 산출물이 그대로 나오는 게 요건이다.

★ **프로바이더 호출과 재시도 · fallback 골격은 ``app.core.llm``이 한다** (2026-09-30 재구성
BL-020 — 골격 ``run_with_fallback``은 여기서 옮겨 갔다). 여기 남은 것은 매입의 몫이다 —
지시문 · 응답 스키마 · 검증기 · ``PURCHASE_`` 설정값과 오류 문장 · 역할(``RoleSpec``).
"""

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from pydantic import ValidationError

from app.contracts.envelope import LLMStatus
from app.core.llm.providers import (
    GEMINI_BASE_URL,
    anthropic_json,
    chat_messages,
    first_text,
    gemini_json_request,
    gemini_parts,
    gemini_request,
    gemini_response_schema,
    ollama_chat_request,
    ollama_request,
    ollama_text,
    openai_json,
    require_model,
    send_json,
)
from app.core.llm.runtime import (
    ENV_FILES,
    OLLAMA_BASE_URL,
    float_env,
    gemini_api_key,
    int_env,
    load_env_files,
    read_bool,
    run_with_fallback,
    scoped_env,
)
from app.purchase_agent.config import load_constraints
from app.purchase_agent.llm.schemas import (
    MIX_PRECEDENCE_SIGNAL,
    GradeMixInterpretation,
    InterpretationResult,
    SanitizedLLMContext,
)
from app.purchase_agent.llm.text_guard import contains_control_chars, contains_number

#: 에이전트 전용 접두사 — ``PURCHASE_LLM_MODEL``로 다른 에이전트와 분리한다 (critic 선례).
ENV_PREFIX = "PURCHASE_"

# 🔴 **``SPREAD_NORMAL`` 절을 걷어냈다** (2026-09-18 · 검증설계 v0.2 §4 · 결정 ②).
#   *"SPREAD_NORMAL이면 신선도 리스크가 이득을 넘기 쉽다"* 는 **죽은 분기**였다 —
#   이 판단자가 불리는 날은 언제나 ``SPREAD_WIDE`` 다. ``_select_mix`` 가
#   ``rule_ratio <= 0`` 이면 돌아가고, ``rule_ratio`` 는 ``widened`` 가 참일 때만 선다.
#   ⇒ 안 오는 상황을 계속 읽히면 판단자가 그 문장으로 사유를 짓는다. 지운다.
SYSTEM_PROMPT = """당신은 매입 에이전트의 등급 조합 판단 레이어다.
계산은 이미 끝났다. 규칙이 만든 후보 중 **하나를 고르고 이유를 쓰는 것**이 전부다.

규칙:
- 반드시 주어진 candidates의 candidate_id 중 하나를 고른다. 새 id를 만들지 않는다.
- 숫자, 비율, 수량, 금액, 날짜를 출력하지 않는다. 라벨과 말로만 설명한다.
- 계산하거나 추정하지 않는다. 수량은 이미 확정돼 있다.
- reason은 한두 문장으로 왜 그 후보인지만 쓴다.
- 지정된 JSON Schema에 맞는 JSON만 출력한다.

판단 기준:
- 중품은 싸지만 잔여신선도 안에 소진해야 한다. 못 쓰면 폐기 손실이다.
- SPREAD_WIDE면 단가 이득이 크다.
- SHELF_TIGHT면 중품 비중을 낮추는 쪽이, SHELF_AMPLE이면 높이는 쪽이 유리하다."""


@dataclass(frozen=True)
class LLMSettings:
    """설정. **API 키를 담지 않는다** — 로그·예외에 새는 경로를 만들지 않기 위해서다."""

    enabled: bool
    provider: str
    model: str
    base_url: str
    timeout_seconds: float
    max_retries: int
    max_output_tokens: int
    effort: str | None
    #: 사유 길이 상한. constraints.yaml이 소유한다 (규칙 7) — 환경변수가 아니라
    #: 도메인 설정이라 ``.env``가 아니라 YAML에서 온다.
    reason_max_chars: int


class PromptContext(Protocol):
    """프로바이더가 컨텍스트에 요구하는 것 — **직렬화되는 것** 하나뿐이다.

    🔴 ``Any`` 로 두지 않는다. 역할마다 컨텍스트 모델이 다르지만 프로바이더가 쓰는 면은
    이 한 줄이고, 넓게 열어 두면 «무엇을 보내는지» 가 타입에서 사라진다.
    """

    def model_dump(self, *, mode: str = ...) -> dict[str, Any]: ...


class LLMProvider(Protocol):
    """문자열을 받아오는 것까지가 프로바이더의 일이다. 검증은 서비스가 한다."""

    def generate(
        self,
        context: PromptContext,
        *,
        retry_guidance: list[str] | None = None,
    ) -> str: ...


def _user_payload(
    context: PromptContext, retry_guidance: list[str] | None
) -> str:
    payload: dict[str, Any] = {"context": context.model_dump(mode="json")}
    if retry_guidance:
        payload["correction"] = retry_guidance
    return json.dumps(payload, ensure_ascii=False)


@dataclass(frozen=True)
class RoleSpec:
    """프로바이더가 알아야 하는 **역할의 전부**.

    🔴 **역할별 분기를 프로바이더 안에 넣지 않는다.** 프로바이더는 *"이 지시문과 이 응답
    스키마로 한 번 물어본다"* 까지만 하고, 무엇을 묻는지는 모른다. 분기를 안에 넣으면
    역할이 늘 때마다 세 프로바이더를 다 고치게 되고, 그 셋은 SDK 사정으로 이미 서로 다르다.

    ⚠️ **요청 모델·검증 함수는 여기 없다.** 그건 역할이 각자 갖는다 — 프로바이더는
    받은 컨텍스트를 직렬화해 보내기만 한다.
    """

    system_prompt: str
    response_schema: dict[str, Any]
    #: 🔴 **지시문과 응답 계약의 판.** 같이 사는 값이라 같은 자리에 둔다 — 흔적에만 적어
    #: 두면 지시문을 고치면서 판을 안 올리는 날이 온다.
    #:
    #: ⚠️ 이 값은 **부른 호출에만** 적힌다. 안 부른 호출(꺼짐·게이트·상한)에서는 빈
    #: 문자열이고, 그 빈칸이 곧 «그 판이 없었다» 는 뜻이다 (``LLMCallMetadata``).
    prompt_version: str = "0"
    schema_version: str = "0"



def _response_schema() -> dict[str, Any]:
    """구조화 출력에 넘길 JSON Schema.

    ``extra="forbid"``라 Pydantic이 ``additionalProperties: false``를 넣어준다 —
    Anthropic 구조화 출력과 OpenAI strict 모드가 **둘 다 요구하는** 항목이다.
    """
    return GradeMixInterpretation.model_json_schema()


#: ⑤ 등급 조합. 🔴 **값은 지금 쓰던 것 그대로다** — 이 판은 «따로 담았을 뿐» 이다.
MIX_ROLE = RoleSpec(
    system_prompt=SYSTEM_PROMPT,
    response_schema=_response_schema(),
    # 🔴 **E3-2 이후 안 바뀐 판이다.** 올리는 것은 지시문이나 응답 계약을 고치는 날이다.
    prompt_version="mix-2",
    schema_version="mix-1",
)


class AnthropicProvider:
    """Messages API + 구조화 출력(``output_config.format``) — ``core.llm.anthropic_json``."""

    def __init__(self, settings: LLMSettings, spec: RoleSpec = MIX_ROLE):
        self.settings = settings
        self.spec = spec

    def generate(
        self,
        context: PromptContext,
        *,
        retry_guidance: list[str] | None = None,
    ) -> str:
        # effort는 **설정했을 때만** 싣는다 — 지원하지 않는 모델에 실으면 호출이 통째로 실패하고,
        # 서비스가 그걸 삼켜 규칙 기본안으로 떨어뜨린다(LLM을 켜 뒀는데 매번 fallback).
        return anthropic_json(
            provider=self.settings.provider,
            model=self.settings.model,
            system=self.spec.system_prompt,
            user=_user_payload(context, retry_guidance),
            schema=self.spec.response_schema,
            max_tokens=self.settings.max_output_tokens,
            timeout=self.settings.timeout_seconds,
            effort=self.settings.effort,
        )


class OpenAIProvider:
    """Chat Completions + ``response_format`` json_schema(strict) — ``core.llm.openai_json``.

    스키마 이름은 역할과 상관없이 ``grade_mix_interpretation``이다(옮기기 전 그대로). 토큰
    상한을 여기도 건다 — 안 걸면 설정값이 무시된 장문 생성이 가능하다(Codex 교차검증).
    """

    def __init__(self, settings: LLMSettings, spec: RoleSpec = MIX_ROLE):
        self.settings = settings
        self.spec = spec

    def generate(
        self,
        context: PromptContext,
        *,
        retry_guidance: list[str] | None = None,
    ) -> str:
        return openai_json(
            provider=self.settings.provider,
            model=self.settings.model,
            system=self.spec.system_prompt,
            user=_user_payload(context, retry_guidance),
            schema=self.spec.response_schema,
            schema_name="grade_mix_interpretation",
            max_tokens=self.settings.max_output_tokens,
            timeout=self.settings.timeout_seconds,
        )


class OllamaProvider:
    """Ollama ``/api/chat``. ``num_predict``가 출력 토큰 상한이다 — 세 프로바이더가 같은
    설정값을 각자의 이름으로 받는다. 전송 실패(``HTTPError`` 포함)는 매입 문장으로 감싼다."""

    def __init__(self, settings: LLMSettings, spec: RoleSpec = MIX_ROLE):
        self.settings = settings
        self.spec = spec

    def generate(
        self,
        context: PromptContext,
        *,
        retry_guidance: list[str] | None = None,
    ) -> str:
        payload = ollama_request(
            self.settings.model,
            chat_messages(self.spec.system_prompt, _user_payload(context, retry_guidance)),
            response_format=self.spec.response_schema,
            options={
                "temperature": 0,
                "num_ctx": 4096,
                "num_predict": self.settings.max_output_tokens,
            },
        )
        document = send_json(
            ollama_chat_request(self.settings.base_url, payload),
            timeout=self.settings.timeout_seconds,
            failure_message="Purchase Local LLM request failed",
        )
        return ollama_text(
            document, missing_message="Purchase Local LLM response did not contain message content"
        )


def _gemini_text(document: dict[str, Any]) -> str:
    """응답에서 **첫 텍스트 조각**을 집는다 — 공백뿐인 조각과 dict 가 아닌 조각은 건너뛴다.

    ⚠️ **사고(``thought``) 조각을 따로 건너뛰지 않는다** — 글자가 없는 사고 조각은 위 규칙으로
    넘어가지만, 글자가 있는 사고 조각은 그대로 집는다. 마스터 · Critic · 재무와 다르다
    (2026-09-30 BL-020 확인 — 바꾸지 않았다). 첫 조각만 읽으면 ``text``가 없어 터지고, 그러면
    **호출은 성공했는데 FALLBACK으로** 떨어진다(마스터 실측 12번 중 11번).
    """
    text = first_text([part for part in gemini_parts(document) if isinstance(part, dict)])
    if text is None:
        raise TypeError("Gemini response contained no text part")
    return text


class GeminiProvider:
    """Gemini REST 호출 (``OllamaProvider``와 같은 규율 — ``core.llm``).

    **키를 어디서 읽나** — ``PURCHASE_GEMINI_API_KEY`` → ``GEMINI_API_KEY``.
    팀 관례 그대로다 (마스터 ``MASTER_`` · 판매 ``SALES_`` · 재무 ``FINANCE_``가 모두
    전용 키 → 공용 키 순서다).

    🔴 **공용 키로 떨어지면 팀 공용 한도를 같이 쓴다.** 전용 키를 안 넣고 provider만
    ``gemini``로 바꾸면 호출이 **조용히 성공하면서** 마스터·재무·판매가 쓰는 같은 한도를
    깎는다. 그래서 전용 키를 넣는 것이 기본이고, 공용 폴백은 "브랜치만 받아도 돈다"를
    위한 것이다 — ``.env.example``에 같은 말을 적어 두었다.

    ⚠️ **키가 둘 다 없어도 여기서 그래프가 죽지 않는다.** 예외는 ``generate()`` 안에서
    나고, ``run_with_fallback``이 받아 **규칙 기본안**으로 보낸다. 조립(``build_provider``)
    은 키를 안 본다 — 미지원 provider에서 ``build_graph()``가 죽던 자리와 같은 결이다.

    🔴 **``LLM_PROVIDER``만 바꾸면 모델 이름이 안 따라온다.** ``_DEFAULT_MODELS``의
    provider별 기본값은 ``LLM_MODEL``이 **비어 있을 때만** 쓰인다. ``.env``에
    ``LLM_MODEL=gemma3:4b``가 있는 채로 provider만 ``gemini``로 바꾸면 **Gemini에게 ollama
    모델 이름을 보내고 404**를 받는다.

    ⚠️ 그 404는 ``HTTPError``라 **감싸지 않고 그대로 올린다** — 그래야 "모델 이름이 틀렸다"가
    "키가 틀렸다"나 "서버가 죽었다"와 구분된다(429 한도 초과와 서버 다운도 마찬가지다).
    나머지 전송 실패만 매입 문장으로 감싼다. 주소는 ``PURCHASE_GEMINI_BASE_URL`` →
    ``GEMINI_BASE_URL`` → 기본값이다(``LLM_BASE_URL``은 Ollama 주소라 읽지 않는다).
    """

    def __init__(self, settings: LLMSettings, spec: RoleSpec = MIX_ROLE):
        self.settings = settings
        self.spec = spec

    def generate(
        self,
        context: PromptContext,
        *,
        retry_guidance: list[str] | None = None,
    ) -> str:
        api_key = gemini_api_key(ENV_PREFIX)
        if not api_key:
            # 🔴 **어느 이름을 봤는지 적는다.** "키가 없다"만 적으면 받는 사람이 전용
            #   키와 공용 키 중 무엇을 넣어야 하는지 모른다. 값은 싣지 않는다.
            raise RuntimeError(
                f"Neither {ENV_PREFIX}GEMINI_API_KEY nor GEMINI_API_KEY is set"
            )
        require_model(self.settings.provider, self.settings.model)
        payload = gemini_json_request(
            self.spec.system_prompt,
            _user_payload(context, retry_guidance),
            gemini_response_schema(self.spec.response_schema),
            max_output_tokens=self.settings.max_output_tokens,
        )
        request = gemini_request(
            self.settings.model,
            payload,
            api_key=api_key,
            base_url=scoped_env(ENV_PREFIX, "GEMINI_BASE_URL", GEMINI_BASE_URL),
        )
        document = send_json(
            request,
            timeout=self.settings.timeout_seconds,
            failure_message="Purchase Gemini request failed",
            keep_http_errors=True,
        )
        return _gemini_text(document)


class UnavailableProvider:
    """미지원 ``LLM_PROVIDER`` 값. 조용히 무시하지 않고 **터뜨려 fallback으로 보낸다**.

    🔴 **다른 셋과 같은 모양으로 받는다** ``(settings, spec)``. 안 쓰는 값이지만 받는다 —
    부르는 쪽이 *"아는 provider 면 이렇게, 모르면 저렇게"* 로 두 모양을 쓰면 **모르는
    provider 일 때만 터지는 길**이 생긴다. 실제로 그랬다: ④·⑧ 이 역할을 넘기는 모양으로
    적었는데 이 클래스만 인자를 안 받아, ``LLM_PROVIDER`` 가 오타일 때 ``build_graph()``
    자체가 ``TypeError`` 로 죽었다. **기본 경로가 돌아야 한다**는 이 파일의 전제가 거기서
    깨진다 — 그래서 지금은 ``build_provider`` 하나로만 만든다.
    """

    def __init__(self, settings: "LLMSettings | None" = None, spec: RoleSpec = MIX_ROLE):
        self.settings = settings
        self.spec = spec

    def generate(
        self,
        context: PromptContext,
        *,
        retry_guidance: list[str] | None = None,
    ) -> str:
        del context, retry_guidance
        raise RuntimeError("Configured purchase LLM provider is not supported")


PROVIDERS: dict[str, type] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "ollama": OllamaProvider,
    "gemini": GeminiProvider,
}


def build_provider(settings: LLMSettings, spec: RoleSpec = MIX_ROLE) -> LLMProvider:
    """설정이 가리키는 프로바이더를 만든다. **모르는 이름이면 터뜨리는 것을 돌려준다.**

    🔴 **역할마다 이 조립을 베끼지 않는다.** 베끼면 한 자리가 다른 모양으로 적히고, 그
    차이는 **설정이 어긋난 날에만** 드러난다 — 평소 경로에서는 셋 다 같은 값을 돌려주기
    때문이다. 조립을 한 함수로 모으는 것이 그 구간을 없애는 방법이다.

    ⚠️ **여기서 예외를 내지 않는다.** 모르는 provider 는 «지금 못 부른다» 이지 «그래프를
    세운다» 가 아니다. 터지는 자리는 호출 시점이고, 그 예외는 ``run_with_fallback`` 이
    받아 **규칙 기본안**으로 보낸다.
    """
    factory = PROVIDERS.get(settings.provider)
    if factory is None:
        return UnavailableProvider(settings, spec)
    return factory(settings, spec)


class ValidationIssue(StrEnum):
    INVALID_SCHEMA = "INVALID_SCHEMA"
    NUMERIC_OUTPUT_FORBIDDEN = "NUMERIC_OUTPUT_FORBIDDEN"
    UNKNOWN_CANDIDATE = "UNKNOWN_CANDIDATE"
    REASON_TOO_LONG = "REASON_TOO_LONG"
    EMPTY_FIELD = "EMPTY_FIELD"
    CONTROL_CHARACTERS = "CONTROL_CHARACTERS"


class MixValidationError(ValueError):
    def __init__(self, issues: list[ValidationIssue]):
        super().__init__(", ".join(issues))
        self.issues = issues


def validate_interpretation(
    raw_output: str, context: SanitizedLLMContext, *, reason_max_chars: int
) -> GradeMixInterpretation:
    """**프로바이더 밖의 공통 검증.** 어느 API를 쓰든 같은 관문을 지난다.

    구조화 출력이 스키마를 강제해도 이 검사가 필요하다 — 스키마는 "문자열 필드가 있다"까지
    보장할 뿐, 그 문자열이 **실재하는 후보 id인지**도 **숫자가 없는지**도 모른다.
    """
    try:
        interpretation = GradeMixInterpretation.model_validate_json(raw_output)
    except ValidationError as error:
        raise MixValidationError([ValidationIssue.INVALID_SCHEMA]) from error
    issues = _validation_issues(interpretation, context, reason_max_chars)
    if issues:
        raise MixValidationError(issues)
    return interpretation


def _validation_issues(
    interpretation: GradeMixInterpretation,
    context: SanitizedLLMContext,
    reason_max_chars: int,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    # 빈/공백 문자열. 스키마로 못 막는다 — 두 API의 JSON Schema가 문자열 길이 제약을
    # 지원하지 않아 ``min_length``를 뺐다 (schemas.GradeMixInterpretation 참조).
    # ``reason``이 비면 근거 없는 판단이 출력에 실리고, id가 비면 후보 대조가 무의미해진다.
    if not interpretation.chosen_candidate_id.strip() or not interpretation.reason.strip():
        issues.append(ValidationIssue.EMPTY_FIELD)
    # 후보 밖 id는 곧 "LLM이 비율을 지어냈다"와 같다 — 노드가 그 id로 비율을 못 찾는다.
    known = {candidate.candidate_id for candidate in context.candidates}
    if interpretation.chosen_candidate_id not in known:
        issues.append(ValidationIssue.UNKNOWN_CANDIDATE)
    # chosen_candidate_id는 검사 대상이 아니다 — 후보 id에 숫자가 들어갈 수 있다.
    if contains_number(interpretation.reason):
        issues.append(ValidationIssue.NUMERIC_OUTPUT_FORBIDDEN)
    if contains_control_chars(interpretation.reason) or contains_control_chars(
        interpretation.chosen_candidate_id
    ):
        issues.append(ValidationIssue.CONTROL_CHARACTERS)
    if len(interpretation.reason) > reason_max_chars:
        issues.append(ValidationIssue.REASON_TOO_LONG)
    return issues


def retry_guidance(issues: list[ValidationIssue]) -> list[str]:
    guidance = []
    if ValidationIssue.INVALID_SCHEMA in issues:
        guidance.append("지정된 두 필드만 포함한 유효한 JSON을 작성하세요.")
    if ValidationIssue.UNKNOWN_CANDIDATE in issues:
        guidance.append("candidates에 있는 candidate_id 중 하나를 그대로 사용하세요.")
    if ValidationIssue.NUMERIC_OUTPUT_FORBIDDEN in issues:
        guidance.append("reason에 숫자를 쓰지 마세요. 라벨과 말로만 설명하세요.")
    if ValidationIssue.REASON_TOO_LONG in issues:
        guidance.append("reason을 한두 문장으로 줄이세요.")
    if ValidationIssue.EMPTY_FIELD in issues:
        guidance.append("두 필드 모두 비어 있지 않게 작성하세요.")
    if ValidationIssue.CONTROL_CHARACTERS in issues:
        guidance.append("보이지 않는 제어문자를 넣지 말고 일반 텍스트로만 작성하세요.")
    return guidance


def needs_llm(context: SanitizedLLMContext) -> bool:
    """**후보가 둘 이상일 때만 부른다** — 고를 게 하나면 물어볼 이유가 없다.

    ⚠️ **이 검사만으로는 평시 호출이 0회가 되지 않는다.** ``cap_ratio``는 스프레드와
    무관하게 근접 납품량으로 계산되므로 평시에도 후보가 3개 나온다(실측). 비용 완화의
    본체는 노드 쪽 게이팅이다 — ``_select_mix``가 "규칙이 중품을 채택한 날"(rule_ratio > 0)
    에만 여기까지 온다. 이 함수는 그 뒤의 **마지막 안전장치**다.

    🔴 **예외 하나 — 우열표가 좁힌 날은 하나여도 부른다** (2026-09-15 · E3-12). 그날은
    «고를 게 없어서» 하나인 것이 아니라 **규칙이 정해서** 하나이고, 판단자가 할 일이
    «고르기» 에서 «설명하기» 로 바뀐다. 안 부르면 산출물이 조용히 달라진다 — 근거에서
    「등급 조합 … 선택 — <사유>」 한 줄이 빠지고 위험에 「판단 미적용」이 대신 뜬다
    (``package_scenarios`` 가 ``mix`` 의 세 상태를 그렇게 가른다).

    ⚠️ 원래 후보가 하나뿐이던 날은 여기까지 오지 않는다 — 노드가 «좁히기 전» 개수로
    먼저 막는다. 두 경우를 한 조건으로 합치면 그 구분이 사라진다.
    """
    return len(context.candidates) >= 2 or MIX_PRECEDENCE_SIGNAL in context.signals


def _mix_guidance(error: Exception) -> list[str]:
    """⑤ 의 **오류 분류**. 검증 실패는 무엇이 틀렸는지 되돌려 주고, 그 밖은 형식만 짚는다.

    🔴 역할마다 다르므로 골격에 안 넣는다 — 골격은 *"실패하면 이걸 불러 안내를 받는다"*
    까지만 안다.
    """
    if isinstance(error, MixValidationError):
        return retry_guidance(error.issues)
    return ["지정된 규칙과 JSON 형식에 맞춰 다시 작성하세요."]


class MixSelectionService:
    """⑤ 의 설정 — 재시도·fallback 골격은 ``core.llm`` 의 ``run_with_fallback`` 이 소유한다."""

    def __init__(self, settings: LLMSettings, provider: LLMProvider):
        self.settings = settings
        self.provider = provider

    def select(
        self, context: SanitizedLLMContext, *, default_candidate_id: str
    ) -> InterpretationResult:
        """후보 하나를 고른다. **실패하면 규칙 기본안을 그대로 돌려준다.**

        ``default_candidate_id``는 규칙이 고르던 값이라, LLM이 전면 실패해도 산출물이
        E3-1 시절과 동일해진다 — 회귀가 아니라 무변화다.
        """
        template = GradeMixInterpretation(
            chosen_candidate_id=default_candidate_id,
            reason="규칙 기본안",
        )
        # 🔄 **골격은 ``run_with_fallback`` 이 소유한다** (2026-09-14). 재시도·오류 분류·
        #   fallback 은 역할이 늘어도 같은데, 여기 두면 역할마다 같은 루프를 베끼게 된다
        #   — 팀 4벌이 이미 그렇게 갈렸다. 아래 셋만 ⑤ 의 것이다.
        해석, 상태, 시도, 떨어짐 = run_with_fallback(
            enabled=self.settings.enabled,
            needs_call=needs_llm(context),
            max_retries=self.settings.max_retries,
            call=lambda guidance: self.provider.generate(context, retry_guidance=guidance),
            validate=lambda raw: validate_interpretation(
                raw, context, reason_max_chars=self.settings.reason_max_chars
            ),
            template=template,
            guidance_for=_mix_guidance,
        )
        return self._result(해석, status=상태, attempts=시도, fallback=떨어짐)

    def _result(
        self,
        interpretation: GradeMixInterpretation,
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


#: 프로바이더별 기본 모델. ``LLM_MODEL``로 덮어쓴다.
#: **openai에는 기본값을 두지 않는다** — 확인하지 않은 모델 id를 코드에 박으면 404를
#: fallback으로 삼키게 되고, 그건 "LLM이 실패했다"와 "모델명을 지어냈다"를 뒤섞는다.
#: 값이 없으면 프로바이더가 즉시 터지고 사유가 risks에 남는다.
#: 프로바이더별 기본 모델. **openai만 일부러 비어 있다** — 확인하지 않은 모델 id를 박으면
#: 404가 fallback에 삼켜져 "LLM이 실패했다"와 "모델명이 틀렸다"가 구분되지 않는다.
#:
#: anthropic 기본이 **Haiku급인 이유** (모델 목록 2026-08-26 재확인): ⑤가 LLM에 맡기는 일은
#: 규칙이 만든 후보 3개 중 하나를 고르고 사유 한 문장을 쓰는 것이 전부다 — 숫자는 규칙이
#: 만든다(규칙 6). 판단 밀도가 낮은데 백테스트는 회당 품목 수만큼 호출하므로, 여기서 상위
#: 모델을 쓰면 비용·지연만 곱해진다. 상위 모델은 ③ 트레이드오프 서술처럼 서술 밀도가
#: 높은 자리에 남겨둔다.
#:
#: **날짜 붙은 스냅샷을 쓴다.** ``claude-haiku-4-5``는 스냅샷을 가리키는 별칭이라 가리키는
#: 대상이 바뀔 수 있다. 같은 as_of로 두 번 돌렸는데 결과가 다르면 백테스트 성적이 무효가
#: 되는 건 규칙 1의 look-ahead와 같은 종류의 문제다 — 재현되지 않는 실행은 근거가 못 된다.
_DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "ollama": "gemma3:4b",  # 팀 4벌의 기본값과 같다
    #: 🔴 **팀이 고른 것과 같은 모델을 쓴다** — 마스터·재무·물류가 전부 이것을 pin 한다.
    #: 파트마다 다른 모델을 쓰면 "모델이 달라서 그런가"가 모든 조사에 끼어든다.
    #: `latest`·`preview` 같은 자동 갱신 별칭은 출력 성향이 예고 없이 바뀌므로 안 쓴다.
    "gemini": "gemini-3.5-flash-lite",
}


def get_llm_settings() -> LLMSettings:
    """``.env``에서 읽는다. **키는 여기 담지 않는다** — 프로바이더가 호출 직전에 읽는다.

    ⚠️ ``timeout_seconds``는 **총 벽시계 deadline이 아니다.** SDK에는 HTTP 단계별
    타임아웃으로, Ollama에는 소켓 타임아웃으로 전달된다. 서비스 재시도까지 더하면 최악의
    경우 설정값의 두 배를 넘을 수 있다 (Codex 교차검증). 총 deadline이 필요해지면
    별도 장치가 있어야 하고, 이 값 하나로는 보장되지 않는다.
    """
    load_env_files(ENV_FILES)
    # ⚠️ provider 가 전역과 달라도 전역 ``LLM_MODEL`` 을 그대로 상속한다 — 마스터 · Critic ·
    #   물류(``resolve_provider_model`` — 그때는 전역 모델을 건너뛴다)와 다르다(옮기기 전 그대로).
    provider = scoped_env(ENV_PREFIX, "LLM_PROVIDER", "anthropic").strip().lower()
    return LLMSettings(
        enabled=read_bool("LLM_ENABLED", prefix=ENV_PREFIX, default=True),
        provider=provider,
        model=scoped_env(ENV_PREFIX, "LLM_MODEL", _DEFAULT_MODELS.get(provider, "")).strip(),
        base_url=scoped_env(ENV_PREFIX, "LLM_BASE_URL", OLLAMA_BASE_URL).rstrip("/"),
        # 파싱 실패는 기본값으로 되돌린다 — ``int("삼십")``이 터지면 ``build_graph()``가
        # 게이팅 이전에 멈춘다(``LLM_ENABLED=false``여도). ``.env`` 오타 하나로 877건이 죽는 건
        # "키·서버 없어도 돈다"는 요건 위반이다 (Codex 교차검증 P2).
        timeout_seconds=float_env(ENV_PREFIX, "LLM_TIMEOUT_SECONDS", "30", minimum=0.1),
        max_retries=min(1, int_env(ENV_PREFIX, "LLM_MAX_RETRIES", "1", minimum=0)),
        max_output_tokens=int_env(ENV_PREFIX, "LLM_MAX_OUTPUT_TOKENS", "8192", minimum=256),
        # **기본은 미설정이다.** effort는 모델마다 지원 여부가 다르고 기본 모델인
        # Haiku 4.5는 지원 목록에 없다. 기본 모델이 이미 최저 티어인 이상 비용 레버는
        # effort가 아니라 모델 선택이다 — 상위 모델로 올릴 때만 켠다.
        effort=(scoped_env(ENV_PREFIX, "LLM_EFFORT", "").strip() or None),
        # 도메인 임계는 .env가 아니라 constraints.yaml에서 온다 (규칙 7).
        reason_max_chars=load_constraints()["grade"]["mix_reason_max_chars"],
    )



def get_mix_selection_service() -> MixSelectionService:
    settings = get_llm_settings()
    return MixSelectionService(settings, build_provider(settings, MIX_ROLE))
