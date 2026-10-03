"""Finance LLM 전송 계층 — 설정 · 가용성 판별 · 재무 요청 모양.

이 파일이 소유하는 것
    Finance LLM 설정(활성화 · Provider · 모델) · Gemini/Ollama 에 보낼 재무 요청 모양
    (JSON 선택 · tool calling) · 응답에서 재무가 읽는 것 · 가용성 실패 판별

여기 없는 것
    무엇을 부를지의 판단 · 재무 계산 · 설명 선택
    → `llm/planner.py` · `service/capabilities/` · `llm/finalizer.py` 소유다.
    요청을 보내는 줄(HTTP) · Gemini 스키마 낮추기 → `app.core.llm`

가용성 실패만 Provider 대체 사유다. 429·5xx·타임아웃·네트워크·키 없음은 "지금 못 부른다"
이고, 그 외 오류는 "불렀는데 답이 틀렸다" 라 대체로 숨기면 안 된다 — 다른 Provider 로 옮겨도
같은 답이 나온다.

설정이 여기 있는 이유: "어느 Provider 로 어떤 모델을 부르는가" 는 전송의 일부다. 전역
`LLM_PROVIDER` 를 상속하지 않는다 — 전역을 ollama 로 둔 배포에서 재무가 조용히 Gemini 를
떠나면 값은 멀쩡히 나오고 아무도 눈치채지 못한다.

재시도하지 않는다 — 대체(Gemini → Ollama)는 `llm/planner.py` 의 가용성 대체가 한 번 한다.
"""

import json
import os
import urllib.error
from typing import Any

from app.core.llm.providers import (
    chat_messages,
    first_text,
    gemini_json_request,
    gemini_parts,
    gemini_request,
    gemini_safe_schema,
    gemini_tool_request,
    ollama_chat_request,
    ollama_message,
    ollama_request,
    send_json,
)
from app.core.llm.runtime import (
    OLLAMA_BASE_URL,
    gemini_api_key,
    read_optional_bool,
)
from app.core.settings import load_env_file

# ---------------------------------------------------------------------------
# Finance LLM 설정
# ---------------------------------------------------------------------------

DEFAULT_MODELS = {
    "ollama": "gemma3:4b",
    "gemini": "gemini-3.5-flash-lite",
}

#: Ollama 로 Planner 를 돌릴 때의 기본 모델. `DEFAULT_MODELS["ollama"]` 와 일부러 다르다.
#:
#: 재무 Planner 는 tool calling 으로 돈다. `gemma3` 계열은 Ollama 에서 tool 을 지원하지 않아
#: 선언을 실으면 HTTP 400 (`does not support tools`) 이다. 그러면 Gemini 가 못 뜰 때 대체가
#: 같이 죽는다 — 대체 경로는 대체가 필요한 날에만 도는 코드라 설정만으로는 이 사실이 드러나지
#: 않는다.
#:
#: 설명(Finalizer)은 tool calling 이 아니라서 기본값을 그대로 쓴다. 여기서 정하는 것은 Tool 을
#: 부르는 자리뿐이다.
#:
#: 추론형(thinking) 모델은 고르지 않는다. 대체는 Gemini 가 못 뜬 날에 도는 경로라
#: `LLM_TIMEOUT_SECONDS` 안에 답해야 뜻이 있다 — 한 단계에 30 초를 넘기면 대체가 있어도
#: 실행은 그대로 실패한다.
_DEFAULT_OLLAMA_TOOL_CALLING_MODEL = "llama3.2:3b"


def ollama_tool_calling_model() -> str:
    """Ollama Planner 모델. 설치된 모델은 배포마다 다르므로 재무 키로 덮을 수 있다."""
    load_env_file()
    return (
        os.getenv("FINANCE_OLLAMA_PLANNER_MODEL")
        or _DEFAULT_OLLAMA_TOOL_CALLING_MODEL
    )


def finance_llm_enabled() -> bool:
    """Finance Agent LLM 활성화 여부.

    ``FINANCE_LLM_ENABLED`` → ``LLM_ENABLED`` → 기본 활성. 재무만 끄고 싶은 경우와
    전역으로 끈 경우를 구분한다 (재무 전용 키가 전역 키를 이긴다).

    주의: 빈 값은 꺼짐이다(미설정이 아니다 — `read_optional_bool`). 마스터 · 물류처럼 전용 키가
    비었을 때 전역으로 넘어가지 않는다.
    """
    load_env_file()
    finance = read_optional_bool("FINANCE_LLM_ENABLED")
    if finance is not None:
        return finance
    shared = read_optional_bool("LLM_ENABLED")
    if shared is not None:
        return shared
    return True


def finance_provider_name() -> str:
    """전역 ``LLM_PROVIDER`` 를 상속하지 않는다.

    전역은 레거시 Ollama 해석 계층이 쓰는 값이다. 그것을 상속하면 전역을 ollama 로
    둔 배포에서 재무 Agent 가 조용히 Gemini 를 떠난다 — 재무 Provider 정책은 재무
    키로만 정해진다.
    """
    load_env_file()
    provider = (
        os.getenv("FINANCE_LLM_PROVIDER")
        or "gemini"
    ).strip().lower()
    if provider not in DEFAULT_MODELS:
        raise RuntimeError("Configured Finance LLM provider is not supported")
    return provider


def finance_model(provider: str) -> str:
    load_env_file()
    explicit = os.getenv("FINANCE_LLM_MODEL")
    if explicit:
        return explicit
    global_provider = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
    global_model = os.getenv("LLM_MODEL")
    if provider == global_provider and global_model:
        return global_model
    return DEFAULT_MODELS[provider]


def finance_planner_model(provider: str) -> str:
    """Planner 가 실제로 부를 모델.

    재무 전용 설정(`FINANCE_LLM_MODEL`)이 있으면 그대로 따른다 — 운영자가 고른 모델을 우리가
    덮지 않는다.

    설정이 없을 때 전역 `LLM_MODEL` 을 물려받지 않는다. 전역은 레거시 해석 계층(tool 을 부르지
    않는다)이 쓰는 값이고, 그것을 Planner 가 상속하면 tool 을 지원하지 않는 모델로 tool calling
    을 시도하게 된다.
    """
    load_env_file()
    explicit = os.getenv("FINANCE_LLM_MODEL")
    if explicit:
        return explicit
    if provider == "ollama":
        return ollama_tool_calling_model()
    return DEFAULT_MODELS[provider]


def ollama_base_url() -> str:
    """Ollama 주소 — 공용 `LLM_BASE_URL` 만 본다(재무 전용 키 없음)."""
    return os.getenv("LLM_BASE_URL", OLLAMA_BASE_URL).rstrip("/")


def llm_timeout_seconds() -> float:
    """호출 한 번의 timeout — 공용 `LLM_TIMEOUT_SECONDS` 만 본다. 숫자가 아니면 예외다."""
    return float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))


# ---------------------------------------------------------------------------
# 재무 요청과 가용성 실패 판별
# ---------------------------------------------------------------------------


def _gemini_response_text(document: dict[str, Any]) -> str:
    """사고(`thought`) 조각 · 공백뿐인 조각을 건너뛴 첫 글자를 앞뒤 공백을 떼어 돌려준다."""
    text = first_text(gemini_parts(document), skip_thoughts=True)
    if text is None:
        raise TypeError("Finance Gemini response did not contain text content")
    return text.strip()


def _gemini_key() -> str:
    """`FINANCE_GEMINI_API_KEY` → `GEMINI_API_KEY`.

    없으면 가용성 판별이 `API_KEY_MISSING` 으로 읽는 문장을 낸다(문장이 계약이다).
    """
    load_env_file()
    api_key = gemini_api_key("FINANCE_")
    if not api_key:
        raise RuntimeError("Finance Gemini API key is not set")
    return api_key


def gemini_generate(
    *, model: str, system_prompt: str, user_payload: dict[str, Any], response_schema: dict[str, Any]
) -> str:
    """Gemini 구조화 출력 한 번 — Finalizer 가 쓴다.

    `HTTPError` 는 감싸지 않는다(가용성 판별이 상태 코드를 본다). 나머지 전송 실패는
    `RuntimeError("Finance Gemini request failed")` 로 감싼다(원인은 `__cause__`).
    """
    api_key = _gemini_key()
    payload = gemini_json_request(
        system_prompt, json.dumps(user_payload, default=str), response_schema
    )
    document = send_json(
        gemini_request(model, payload, api_key=api_key),
        timeout=llm_timeout_seconds(),
        failure_message="Finance Gemini request failed",
        keep_http_errors=True,
    )
    return _gemini_response_text(document)


def gemini_availability_failure_reason(error: Exception) -> str | None:
    if isinstance(error, urllib.error.HTTPError):
        if error.code == 403:
            # 403 전체를 가용성 장애로 낮추지 않는다. Gemini가 권한/프로젝트 접근을
            # 명시한 경우만 Ollama로 넘긴다; 다른 403은 계약·요청 오류일 수 있다.
            try:
                document = json.loads(error.read().decode("utf-8"))
                detail = document.get("error", {}) if isinstance(document, dict) else {}
                status = str(detail.get("status", "")).upper()
                message = str(detail.get("message", "")).lower()
            except Exception:  # noqa: BLE001 - body를 못 읽으면 permission으로 추측하지 않는다.
                return None
            permission_phrases = ("project access", "api access", "permission denied")
            is_permission_problem = status == "PERMISSION_DENIED" or any(
                phrase in message for phrase in permission_phrases
            )
            if is_permission_problem:
                return "HTTP_403_PERMISSION_DENIED"
        if error.code == 429:
            return "HTTP_429"
        if 500 <= error.code < 600:
            return "HTTP_5XX"
        return None
    if isinstance(error, TimeoutError):
        return "TIMEOUT"
    if isinstance(error, urllib.error.URLError):
        return "NETWORK_ERROR"
    if (
        isinstance(error, RuntimeError)
        and str(error) == "Finance Gemini API key is not set"
    ):
        return "API_KEY_MISSING"
    if isinstance(error.__cause__, TimeoutError):
        return "TIMEOUT"
    if isinstance(error.__cause__, urllib.error.URLError):
        return "NETWORK_ERROR"
    return None


def _is_gemini_availability_failure(error: Exception) -> bool:
    return gemini_availability_failure_reason(error) is not None


def ollama_availability_failure_reason(error: Exception) -> str | None:
    """Ollama가 지금 Planner 요청을 수행할 수 없는 경우만 분류한다.

    404는 endpoint 또는 model 부재이고, 429/5xx·timeout·network 오류도 provider가
    현재 실행 불가한 상태다. 반면 400은 tool/schema 계약 문제일 수 있으므로 가용성
    장애로 낮추지 않는다.
    """
    if isinstance(error, urllib.error.HTTPError):
        if error.code == 404:
            return "HTTP_404"
        if error.code == 429:
            return "HTTP_429"
        if 500 <= error.code < 600:
            return "HTTP_5XX"
        return None
    if isinstance(error, TimeoutError):
        return "TIMEOUT"
    if isinstance(error, urllib.error.URLError):
        return "NETWORK_ERROR"
    if isinstance(error.__cause__, TimeoutError):
        return "TIMEOUT"
    if isinstance(error.__cause__, urllib.error.URLError):
        return "NETWORK_ERROR"
    return None


def gemini_tool_call(
    *,
    model: str,
    system_prompt: str,
    user_payload: dict[str, Any],
    tool_declarations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Gemini function calling. 이번 호출에서 부를 수 있는 함수만 선언한다.

    ``mode: ANY`` + ``allowedFunctionNames`` 로 자유 문장 답을 닫는다. 다만 이것은
    전송 계층 강제일 뿐이라, 돌아온 이름이 정말 허용된 것인지는 Planner 사후 검증과
    Harness 가 다시 본다 — 구조화 출력을 무시하는 모델이 있다.

    Tool 인자 스키마는 `gemini_safe_schema` 로 표현만 낮춘다(`const` → 한 값 enum · null 갈래 →
    nullable · `additionalProperties` 제거). 그대로 보내면 HTTP 400 이다. `$ref` 는 펴지 않고
    `$defs` 도 남긴다(`inline_refs=False`).
    """
    api_key = _gemini_key()
    names = [item["name"] for item in tool_declarations]
    declarations = [
        {**item, "parameters": gemini_safe_schema(item.get("parameters", {}), inline_refs=False)}
        for item in tool_declarations
    ]
    payload = gemini_tool_request(
        system_prompt,
        [{"role": "user", "parts": [{"text": json.dumps(user_payload, default=str)}]}],
        declarations,
        function_calling={"mode": "ANY", "allowedFunctionNames": names},
    )
    document = send_json(
        gemini_request(model, payload, api_key=api_key),
        timeout=llm_timeout_seconds(),
        failure_message="Finance Gemini request failed",
        keep_http_errors=True,
    )
    return [
        {"name": part["functionCall"].get("name"), "args": part["functionCall"].get("args") or {}}
        for part in gemini_parts(document)
        if isinstance(part.get("functionCall"), dict)
    ]


def ollama_tool_call(
    *,
    model: str,
    system_prompt: str,
    user_payload: dict[str, Any],
    tool_declarations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Ollama tool calling. Gemini 와 같은 Tool 목록을 받는다.

    Provider 마다 허용 범위가 달라지면 같은 재무 상태가 다른 Tool 을 부를 수 있게
    열린다 — 선언은 한 곳(`service/harness.py` 의 `build_tool_adapter`)에서 만들어 양쪽에
    그대로 간다.

    전송 예외를 감싸지 않는다 — 가용성 판별(`ollama_availability_failure_reason`)이 본다.
    """
    body = ollama_request(
        model,
        chat_messages(system_prompt, json.dumps(user_payload, default=str)),
        tools=[{"type": "function", "function": declaration} for declaration in tool_declarations],
        options={"temperature": 0},
    )
    request = ollama_chat_request(ollama_base_url(), body)
    document = send_json(request, timeout=llm_timeout_seconds())
    raw_calls = ollama_message(document).get("tool_calls") or []
    return [
        {
            "name": (item.get("function") or {}).get("name"),
            "args": (item.get("function") or {}).get("arguments") or {},
        }
        for item in raw_calls
    ]
