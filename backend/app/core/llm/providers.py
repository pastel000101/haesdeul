"""LLM 프로바이더 호출 — 외부 LLM 에 요청을 보내는 코드가 사는 유일한 자리.

부서 일곱 곳(재무 · 물류 · 마스터 · Critic · 매입 · 판매 · ML)이 이 전송 코드를 쓴다.

```text
보내기      json_request · ollama_chat_request · gemini_request   요청 만들기(보내지 않는다)
            send_json                                            urlopen 한 곳
            anthropic_json · openai_json                         SDK 한 곳 (지연 import)
요청 본문   chat_messages · ollama_request · gemini_json_request · gemini_tool_request
응답 읽기   ollama_message · ollama_text · gemini_parts · first_text · gemini_first_part_text
스키마      gemini_strict_schema (마스터) · gemini_response_schema (매입) ·
            gemini_safe_schema (판매 · 재무는 inline_refs=False)
```

여기는 부서를 모른다. 무엇을 묻는지(지시문 · 응답 스키마) · 무엇이 옳은지(검증) ·
실패하면 무엇을 할지(재시도 · 대체)는 부서가 정해 인자로 넘긴다. 오류 문장도 부서가 준다 —
문장이 부서마다 다르고, 부르는 쪽 검사 · 분류가 그 문장을 본다.

재시도하지 않는다. SDK 도 `max_retries=0` 이다. 재시도는 부서 실행 순서(`runtime.py` 의
`run_with_fallback` 또는 부서 자체 루프)가 소유한다 — 두 층이 각자 세면 상한이 곱해진다.

요청 만들기와 보내기를 나눈다. 주소가 잘못되면 `Request` 를 만들 때 `ValueError` 가
난다. 부서 대부분은 그 줄을 예외를 삼키는 자리 밖에 둔다(ML 은 그래서 잘못된 주소가
삼켜지지 않고 올라간다). 나눠 두어야 부서가 그 경계를 그대로 둘 수 있다.

부서마다 다른 것은 인자로 받는다 — 그 차이를 여기서 없애지 않는다:

```text
오류 감싸기     Gemini 는 HTTPError 를 감싸지 않고(상태 코드 보존) 나머지만 감싼다.
                Ollama 는 HTTPError 까지 감싼다. 감싸지 않는 부서도 있다(물류 Gemini · 판매 ·
                ML · 재무 Ollama · 물류 status_chat).
응답 글자       thought 조각을 건너뛰나 · 공백뿐인 조각을 받나 · 앞뒤 공백을 떼나가 부서마다 다르다
본문 인코딩     물류 status_chat 만 ensure_ascii=False · default=str 로 보낸다
스키마 변환     부서마다 한 벌씩이던 변환을 결과 · 예외가 같게 셋으로 둔다 — 참조 펴기 · anyOf ·
                버리는 칸 · 예외가 부서마다 다르다(아래 «Gemini 스키마 변환» 절)
```
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any

__all__ = [
    "GEMINI_BASE_URL",
    "anthropic_json",
    "chat_messages",
    "first_text",
    "gemini_first_part_text",
    "gemini_json_request",
    "gemini_parts",
    "gemini_request",
    "gemini_response_schema",
    "gemini_safe_schema",
    "gemini_strict_schema",
    "gemini_tool_request",
    "json_request",
    "ollama_chat_request",
    "ollama_message",
    "ollama_request",
    "ollama_text",
    "openai_json",
    "require_model",
    "send_json",
]

#: Gemini REST 기본 주소. `LLM_BASE_URL` 에서 읽지 않는다 — 그 값의 기본이 Ollama
#: (`127.0.0.1:11434`)라 provider 만 바꾼 사람이 로컬 포트로 쏘고 연결 실패로만 본다.
#: 덮는 환경변수는 부서마다 다르다(부서 `llm/`).
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


# ── 보내기 ──────────────────────────────────────────────────────────────────


def json_request(
    url: str,
    payload: Any,
    *,
    headers: Mapping[str, str] | None = None,
    ensure_ascii: bool = True,
    json_default: Callable[[Any], Any] | None = None,
) -> urllib.request.Request:
    """JSON 본문을 실은 POST 요청을 만든다. 보내지 않는다.

    머리글은 `Content-Type` 뒤에 `headers` 순서로 붙는다.
    """
    return urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=ensure_ascii, default=json_default).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )


def ollama_chat_request(base_url: str, payload: Any, **encoding: Any) -> urllib.request.Request:
    """Ollama `/api/chat` 요청. `base_url` 은 부서 설정이 끝 `/` 를 이미 뗀 값이다."""
    return json_request(f"{base_url}/api/chat", payload, **encoding)


def gemini_request(
    model: str,
    payload: Any,
    *,
    api_key: str,
    base_url: str = GEMINI_BASE_URL,
    **encoding: Any,
) -> urllib.request.Request:
    """Gemini `generateContent` 요청.

    키는 머리글로만 간다. URL 에 실으면 예외 메시지 · 로그에 그대로 남는다.
    """
    return json_request(
        f"{base_url.rstrip('/')}/models/{model}:generateContent",
        payload,
        headers={"x-goog-api-key": api_key},
        **encoding,
    )


def send_json(
    request: urllib.request.Request,
    *,
    timeout: float,
    failure_message: str | None = None,
    keep_http_errors: bool = False,
) -> Any:
    """요청을 보내고 응답 본문을 JSON 으로 읽는다. 외부 LLM HTTP 호출은 여기 한 곳이다.

    :param failure_message: 주면 시간 초과 · 연결 실패 · 깨진 JSON 을
        `RuntimeError(failure_message)` 로 감싼다(원래 예외는 `__cause__`). 안 주면 그대로 올린다.
    :param keep_http_errors: `failure_message` 를 줬을 때 `HTTPError` 만은 감싸지 않는다.

    `HTTPError` 는 `URLError` 의 하위라 감싸는 자리가 같이 잡는다. 감싸면 상태 코드가
    사라진다 — 429(한도)와 서버 다운이 로그에서 같아 보였다(마스터 실측). Gemini 를 부르는
    부서는 그래서 `keep_http_errors=True` 로 부른다.
    """
    if failure_message is None:
        return _read_json(request, timeout)
    try:
        return _read_json(request, timeout)
    except urllib.error.HTTPError as error:
        if keep_http_errors:
            raise
        raise RuntimeError(failure_message) from error
    except (TimeoutError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise RuntimeError(failure_message) from error


def _read_json(request: urllib.request.Request, timeout: float) -> Any:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def require_model(provider: str, model: str) -> None:
    """모델명이 비었으면 부르지 않는다 — 빈 문자열을 API 에 보내면 사유가 흐려진다."""
    if not model:
        raise RuntimeError(f"LLM_MODEL is not set for provider {provider!r}")


def anthropic_json(
    *,
    provider: str,
    model: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    max_tokens: int,
    timeout: float,
    effort: str | None,
) -> str:
    """Anthropic Messages API + 구조화 출력(`output_config.format`). 첫 text 블록을 돌려준다.

    - 키는 호출 시점에 `ANTHROPIC_API_KEY` 에서 읽는다(설정 객체에 싣지 않는다). 키 확인 →
      모델 확인 → 호출 순서다. `provider` 는 모델이 빌 때의 오류 문장에만 쓴다.
    - `effort` 는 설정했을 때만 싣는다 — 지원하지 않는 모델에 실으면 호출이 통째로 실패한다.
    - 주의: `content[0]` 이 아니다 — 사고(thinking) 블록이 앞에 오는 모델이 있다.
    """
    import anthropic  # 지연 import — 키 없는 환경에서 import 비용을 안 낸다

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    require_model(provider, model)
    client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=0)
    output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
    if effort:
        output_config["effort"] = effort
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        output_config=output_config,
        messages=[{"role": "user", "content": user}],
    )
    for block in message.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise TypeError("Anthropic response contained no text block")


def openai_json(
    *,
    provider: str,
    model: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    schema_name: str,
    max_tokens: int,
    timeout: float,
) -> str:
    """OpenAI Chat Completions + `response_format` json_schema(strict). 메시지 본문을 돌려준다.

    키는 호출 시점에 `OPENAI_API_KEY` 에서 읽는다. 출력 토큰 상한의 OpenAI 이름은
    `max_completion_tokens` 다. `schema_name` 은 부서가 준다.
    """
    import openai

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    require_model(provider, model)
    client = openai.OpenAI(api_key=api_key, timeout=timeout, max_retries=0)
    completion = client.chat.completions.create(
        model=model,
        messages=chat_messages(system, user),
        max_completion_tokens=max_tokens,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        },
    )
    content = completion.choices[0].message.content
    if not isinstance(content, str):
        raise TypeError("OpenAI response did not contain message content")
    return content


# ── 요청 본문 ────────────────────────────────────────────────────────────────


def chat_messages(system: str, user: str) -> list[dict[str, str]]:
    """시스템 · 사용자 두 줄. Ollama · OpenAI 가 같은 모양을 받는다."""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def ollama_request(
    model: str,
    messages: list[dict[str, Any]],
    *,
    options: dict[str, Any],
    response_format: Any = None,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Ollama `/api/chat` 본문. `format`(구조화 출력) 또는 `tools`(tool calling)를 싣는다.

    `think: False` · `stream: False` 는 모든 부서가 같다. `options` 는 부서마다 다르다
    (마스터 · 매입은 `num_predict` 로 출력 상한을 싣고, 재무 · 물류 status_chat 은
    `temperature` 만 싣는다).
    """
    body: dict[str, Any] = {"model": model, "stream": False, "think": False}
    if response_format is not None:
        body["format"] = response_format
    if tools is not None:
        body["tools"] = tools
    body["messages"] = messages
    body["options"] = options
    return body


def gemini_json_request(
    system: str,
    user_text: str,
    response_schema: Any,
    *,
    max_output_tokens: int | None = None,
) -> dict[str, Any]:
    """Gemini 구조화 출력 본문 — 사용자 한 턴, 온도 0, JSON 응답.

    `max_output_tokens` 는 마스터 · 매입만 싣는다(`maxOutputTokens`).
    """
    config: dict[str, Any] = {
        "temperature": 0,
        "responseMimeType": "application/json",
        "responseSchema": response_schema,
    }
    if max_output_tokens is not None:
        config["maxOutputTokens"] = max_output_tokens
    return {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": config,
    }


def gemini_tool_request(
    system: str,
    contents: list[dict[str, Any]],
    declarations: list[dict[str, Any]],
    *,
    function_calling: dict[str, Any],
) -> dict[str, Any]:
    """Gemini function calling 본문. `function_calling` 은 `functionCallingConfig` 그대로다
    (재무 `mode: ANY` + 허용 이름 · 물류 status_chat `mode: AUTO`)."""
    return {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": contents,
        "tools": [{"function_declarations": declarations}],
        "toolConfig": {"functionCallingConfig": function_calling},
        "generationConfig": {"temperature": 0},
    }


# ── 응답 읽기 ────────────────────────────────────────────────────────────────


def ollama_message(document: Mapping[str, Any]) -> Mapping[str, Any]:
    """Ollama 응답의 `message`. 없으면 빈 dict."""
    return document.get("message") or {}


def ollama_text(document: Mapping[str, Any], *, missing_message: str) -> str:
    """Ollama 구조화 출력의 본문 글자. 글자가 아니면 `TypeError(missing_message)`."""
    content = ollama_message(document).get("content")
    if not isinstance(content, str):
        raise TypeError(missing_message)
    return content


def gemini_parts(document: Mapping[str, Any]) -> list[Any]:
    """첫 후보의 `content.parts`. 후보 · 내용이 없으면 빈 목록."""
    candidates = document.get("candidates") or []
    return ((candidates[0] if candidates else {}).get("content") or {}).get("parts") or []


def first_text(
    parts: Sequence[Any],
    *,
    skip_thoughts: bool = False,
    allow_whitespace: bool = False,
) -> str | None:
    """조각들 가운데 처음 나오는 글자. 없으면 `None` — 없을 때 무엇을 할지는 부서가 정한다.

    :param skip_thoughts: `thought: true` 조각을 건너뛴다. `parts[0]` 만 보면 사고 조각에
        글자가 없어 실패하고, 호출은 성공했는데 FALLBACK 으로 떨어진다(마스터 실측 12번 중 11번).
    :param allow_whitespace: 공백뿐인 글자도 받는다(빈 문자열은 늘 건너뛴다).

    주의: 조각을 `.get` 으로 읽는다 — dict 가 아닌 조각은 `AttributeError` 다. 그런 조각을
    건너뛰는 부서(매입)는 거른 목록을 넘긴다.
    """
    for part in parts:
        if skip_thoughts and part.get("thought"):
            continue
        text = part.get("text")
        if not isinstance(text, str):
            continue
        if text if allow_whitespace else text.strip():
            return text
    return None


def gemini_first_part_text(document: Mapping[str, Any]) -> Any:
    """`candidates[0].content.parts[0].text` 를 그대로 꺼낸다(물류 해석기 · ML).

    빠진 칸은 `KeyError` · `IndexError` · `TypeError` 로 그대로 올린다 — 부르는 쪽이 감싼다.
    """
    return document["candidates"][0]["content"]["parts"][0]["text"]


# ── Gemini 스키마 변환 ───────────────────────────────────────────────────────
#
# 부서(마스터 · 매입 · 판매 · 재무)마다 결과 · 예외가 다른 변환을 셋으로 둔다. 참조 펴기 · anyOf ·
# 버리는 칸 · 예외가 서로 다르다 — 지금 보내는 스키마에서 결과가 같다고 다른 입력에서도 같지는
# 않으므로, 하나로 합치면 어느 부서의 동작이 바뀐다.
#
#   gemini_strict_schema    마스터  최상위 $defs 만 · X | null 이 아닌 anyOf 거부 · 길이 제약 남김
#   gemini_response_schema  매입    만나는 $defs 를 모음 · 다른 anyOf 는 그대로 · 길이 제약 버림
#   gemini_safe_schema      판매    표현만 낮춘다(title · default 남김 · const → enum) · 참조를 편다
#                           재무    같은 함수에 inline_refs=False — $ref · $defs 를 그대로 둔다

#: 마스터 변환이 버리는 칸. 길이 제약(`minLength` · `maxLength`)은 남긴다 — 매입과 다르다.
_STRICT_SCHEMA_DROP = frozenset({"title", "default", "additionalProperties", "$schema", "examples"})


def gemini_strict_schema(node: Any, defs: dict[str, Any] | None = None) -> Any:
    """JSON Schema → Gemini `responseSchema` (마스터 — 의도 분류 · 응답 문장).

    ```text
    버린다   title · default · additionalProperties · $schema · examples   (길이 제약은 남긴다)
    바꾼다   anyOf[X, null] → X + nullable: true — X | null 이 아닌 anyOf 는 TypeError
    편다     $ref → 최상위 $defs 의 정의 ($ref 옆 칸이 정의를 덮는다)
    ```

    모르는 anyOf 는 예외로 올린다. 조용히 흘리면 Gemini 400 이 호출 실패로만 보인다 — 부르는
    쪽(`IntentService` · `NarrativeService`)이 예외를 FALLBACK 으로 받는다.

    세부 규칙:
      - 참조는 최상위 `$defs` 에서만 찾는다(안쪽 `$defs` 는 버린다). 못 찾거나 정의가 dict 가
        아니면 `TypeError("cannot resolve schema reference: …")`.
      - anyOf 는 다른 칸보다 먼저 본다. 남은 갈래를 먼저 옮기고 옆 칸이 그 위를 덮는다
        (`nullable` 은 맨 뒤). dict 가 아닌 갈래는 갈래로 세지 않는다.
      - 문자열이 아닌 `$ref` 칸은 버린다.

    주의: 재귀 참조는 무한히 펴진다. 지금 쓰는 스키마에는 없다.
    """
    if defs is None and isinstance(node, dict):
        defs = node.get("$defs") or {}
    if isinstance(node, list):
        return [gemini_strict_schema(item, defs) for item in node]
    if not isinstance(node, dict):
        return node

    ref = node.get("$ref")
    if isinstance(ref, str):
        target = (defs or {}).get(ref.rsplit("/", 1)[-1])
        if not isinstance(target, dict):
            raise TypeError(f"cannot resolve schema reference: {ref}")
        merged = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
        return gemini_strict_schema(merged, defs)

    if "anyOf" in node:
        branches = node["anyOf"]
        concrete = [b for b in branches if isinstance(b, dict) and b.get("type") != "null"]
        nullable = len(concrete) != len(branches)
        if len(concrete) != 1:
            raise TypeError(
                f"Gemini 로 옮길 수 없는 anyOf 다 (분기 {len(concrete)}개): {branches!r}"
            )
        converted = gemini_strict_schema(concrete[0], defs)
        for key, value in node.items():
            if key == "anyOf" or key in _STRICT_SCHEMA_DROP or key in {"$defs", "$ref"}:
                continue
            converted[key] = gemini_strict_schema(value, defs)
        if nullable:
            converted["nullable"] = True
        return converted

    return {
        key: gemini_strict_schema(value, defs)
        for key, value in node.items()
        if key not in _STRICT_SCHEMA_DROP and key not in {"$defs", "$ref"}
    }


#: 매입 변환이 버리는 칸 — Gemini `responseSchema` 가 거부하거나 무시하는 키.
#:
#: `minLength`/`maxLength` 도 뺀다 — 매입 응답 계약이 두 제약을 안 쓰는 이유(Anthropic ·
#: OpenAI 가 지원하지 않는다)와 같은 자리이고, 빈 문자열 검사는 프로바이더 밖 검증이 한다.
_RESPONSE_SCHEMA_DROP = frozenset(
    {"title", "default", "additionalProperties", "$schema", "examples", "minLength", "maxLength"}
)


def gemini_response_schema(node: Any, defs: dict[str, Any] | None = None) -> Any:
    """JSON Schema → Gemini `responseSchema`. 버리고 · 바꾸고 · 편다 (매입).

    ```text
    버린다   title · default · additionalProperties · $schema · examples · minLength · maxLength
    바꾼다   anyOf[X, null] → X + nullable: true (null 아닌 갈래가 둘 이상이면 anyOf 로 둔다)
    편다     $ref → $defs 의 정의를 그 자리에 펼친다 ($ref 옆 칸이 정의를 덮는다)
    남긴다   description — 빼면 provider 를 바꾼 것만으로 모델에게 보이는 지시가 달라진다
    ```

    - 못 편 참조는 `KeyError` 다 — 조용히 넘기면 Gemini 400 이 fallback 에 삼켜진다.
    - `$defs` 는 만나는 자리마다 모은다(안쪽 `$defs` 도 쓴다). 칸은 순서대로 옮긴다 — anyOf
      앞의 칸은 갈래가 덮고, 뒤의 칸은 갈래를 덮는다.
    - 주의: 재귀 참조는 무한히 펴진다. 지금 쓰는 스키마에는 없다.

    마스터는 이 변환을 쓰지 않는다(`gemini_strict_schema`) — 길이 제약 · 못 편 참조의
    예외 · 참조를 찾는 범위 · anyOf 옆 칸의 우선이 다르다.
    """
    if isinstance(node, list):
        return [gemini_response_schema(item, defs) for item in node]
    if not isinstance(node, dict):
        return node

    # `$defs` 는 만나는 자리에서 모아 두고 결과에서는 뺀다 — 펼친 뒤엔 참조가 없다.
    defs = {**(defs or {}), **(node.get("$defs") or {})}

    ref = node.get("$ref")
    if isinstance(ref, str):
        name = ref.rsplit("/", 1)[-1]
        target = defs.get(name)
        if target is None:
            raise KeyError(f"gemini 스키마에서 못 펴는 참조다: {ref}")
        siblings = {k: v for k, v in node.items() if k not in {"$ref", "$defs"}}
        return {**gemini_response_schema(target, defs), **gemini_response_schema(siblings, defs)}

    converted: dict[str, Any] = {}
    nullable = False
    for key, value in node.items():
        if key in _RESPONSE_SCHEMA_DROP or key == "$defs":
            continue
        if key == "anyOf":
            branches = [b for b in value if not (isinstance(b, dict) and b.get("type") == "null")]
            nullable = len(branches) != len(value)
            if len(branches) == 1:
                converted.update(gemini_response_schema(branches[0], defs))
            elif branches:
                converted["anyOf"] = [gemini_response_schema(b, defs) for b in branches]
            continue
        converted[key] = gemini_response_schema(value, defs)
    if nullable:
        converted["nullable"] = True
    return converted


def gemini_safe_schema(
    node: Any, defs: Mapping[str, Any] | None = None, *, inline_refs: bool = True
) -> Any:
    """Pydantic 스키마를 Gemini 가 받는 표현으로만 낮춘다 (판매 · 재무). 계약 의미는 그대로다.

    ```text
    const                    → type string + 한 값짜리 enum
    anyOf 안의 type:null     → 그 갈래를 빼고 nullable (한 갈래만 남으면 펼친다)
    additionalProperties     → 버린다
    $ref                     → $defs 의 정의를 펴 넣고 $defs 는 지운다 (inline_refs=False 면 그대로)
    그 밖(title · default …)  → 남긴다
    ```

    :param inline_refs: `False` 면 `$ref` 를 펴지 않고 `$defs` 도 지우지 않는다 — 재무 Tool 인자
        스키마(`finance/llm/client.py`)가 이렇게 부른다. 재무 Tool 인자에는 참조가 없고, 재무
        변환은 참조를 펴지 않는 동작이다. 판매는 편다(기본값).

    - 중첩 모델의 `$defs` · `$ref` 를 펴 넣는다 — Gemini 는 그 둘을 모르고 요청 자체를
      거부한다(펴지 않으면 판매 전략 Planner 가 한 번도 돌지 않았다 · 2026-09-16 실측).
    - 참조는 최상위 `$defs` 에서만 찾는다. 못 편 참조는 `TypeError` 다 — 없는 정의를 지어내면
      계약이 달라진다.
    - 주의: 자기 자신을 참조하는 모델은 못 편다(무한히 돈다). 지금 두 계약에는 없다.
    """
    if defs is None and isinstance(node, dict):
        defs = node.get("$defs") or {}
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if inline_refs and isinstance(ref, str):
        # `#/$defs/Name` 의 마지막 조각이 정의 이름이다.
        # `$ref` 옆 칸(description 등)이 정의를 덮는다.
        target = (defs or {}).get(ref.rsplit("/", 1)[-1])
        if not isinstance(target, Mapping):
            raise TypeError(f"cannot resolve schema reference: {ref}")
        merged = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
        return gemini_safe_schema(merged, defs)
    excluded = {"const", "anyOf", "additionalProperties"}
    if inline_refs:
        excluded.add("$defs")
    safe = {key: value for key, value in node.items() if key not in excluded}
    if "const" in node:
        safe.update({"type": "string", "enum": [node["const"]]})
    if "anyOf" in node:
        branches = [b for b in node["anyOf"] if isinstance(b, dict) and b.get("type") != "null"]
        if len(branches) != len(node["anyOf"]):
            safe["nullable"] = True
        if len(branches) == 1:
            safe.update(gemini_safe_schema(branches[0], defs, inline_refs=inline_refs))
        elif branches:
            safe["anyOf"] = [
                gemini_safe_schema(branch, defs, inline_refs=inline_refs) for branch in branches
            ]
    if "properties" in node:
        safe["properties"] = {
            name: gemini_safe_schema(child, defs, inline_refs=inline_refs)
            for name, child in node["properties"].items()
        }
    if "items" in node:
        safe["items"] = gemini_safe_schema(node["items"], defs, inline_refs=inline_refs)
    return safe
