"""LLM 실행 순서와 설정 해석 — 부서 LLM 이 함께 쓰는 **골격**.

2026-09-30 재구성 BL-020 에 부서 런타임 여섯 벌에 복제돼 있던 것을 모았다. 옮기기 전과
값 · 순서 · 예외가 같다.

```text
run_with_fallback       켜짐 → 부를 조건 → 호출 · 검증 → 재시도 → 기본안
                        (마스터 · Critic · 매입)
ENV_FILES · load_env_files     `.env` 두 자리와 적재
scoped_env · read_bool · read_optional_bool · int_env · float_env
                        `<PREFIX>_` 우선 읽기
resolve_provider_model  provider 와 모델 고르기                        (마스터 · Critic · 물류)
gemini_api_key          `<PREFIX>_GEMINI_API_KEY` → `GEMINI_API_KEY`
```

★ **정책은 부서가 고른다.** 여기 있는 것은 여러 부서가 **글자까지 같게** 쓰던 규칙뿐이다.
  같지 않은 것은 부서에 남겼다(2026-09-30):

```text
재시도        물류는 전송 재시도와 검증 재시도를 따로 센다(자기 루프)
              판매 · ML · 재무는 재시도 없음
대체          재무는 Gemini 가용성 실패 때 Ollama 로 옮긴다(자기 규칙)
provider      재무 · 판매는 전역 LLM_PROVIDER 를 상속하지 않는다
              매입은 전역 모델을 그대로 상속한다
켜짐          재무 · 판매는 read_optional_bool 로 사슬을 직접 잇는다(빈 값 = 꺼짐)
              ML 은 자기 규칙
숫자 설정     Critic 은 잘못된 값에 예외를 낸다 — int_env · float_env 를 쓰지 않는다
.env 위치     Critic(`app/.env`) · 재무(`app/.env` · `backend/.env`)는 다른 파일을 읽는다
```
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from dotenv import load_dotenv

from app.core.settings import ENV_FILE

__all__ = [
    "ENV_FILES",
    "OLLAMA_BASE_URL",
    "TRUE_VALUES",
    "float_env",
    "gemini_api_key",
    "int_env",
    "load_env_files",
    "read_bool",
    "read_optional_bool",
    "resolve_provider_model",
    "run_with_fallback",
    "scoped_env",
]

#: 참으로 읽는 값. `strip().lower()` 뒤에 비교한다.
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

#: Ollama 기본 주소. `LLM_BASE_URL` 이 없을 때의 값이다.
OLLAMA_BASE_URL = "http://127.0.0.1:11434"

#: `backend/.env` 와 저장소 루트 `.env` — 이 순서로 읽는다(없는 파일은 건너뛴다).
#: 팀 환경에서 `.env` 가 루트에 있는 경우 `backend/` 만 보면 키를 못 찾는다.
#: 마스터 · 물류 · 매입 · 판매 · ML 이 이 두 자리를 읽는다.
ENV_FILES: tuple[Path, ...] = (ENV_FILE, ENV_FILE.parent.parent / ".env")


def load_env_files(paths: Iterable[Path], *, override: bool = False) -> None:
    """`.env` 파일들을 차례로 적재한다. 이미 있는 환경변수는 덮지 않는다(`override=False`).

    ★ **부르는 시점은 부서가 고른다** — 설정을 읽을 때마다 부른다(옮기기 전과 같다).
    """
    for path in paths:
        load_dotenv(path, override=override)


def scoped_env(prefix: str, key: str, default: str) -> str:
    """`<PREFIX><KEY>` → `<KEY>` → `default`. **빈 문자열은 없는 것으로 본다**(다음 자리로 간다)."""
    return os.getenv(f"{prefix}{key}") or os.getenv(key) or default


def read_bool(key: str, *, prefix: str = "", default: bool) -> bool:
    """`<PREFIX><KEY>` → `<KEY>` 를 참 · 거짓으로. 둘 다 없으면 `default`.

    ⚠️ 전용 값이 빈 문자열이면 공용 값으로 넘어간다(`or`). 공용 값이 빈 문자열이면 거짓이다.
      재무 · 판매는 뜻이 달라 `read_optional_bool` 을 쓴다.
    """
    value = os.getenv(f"{prefix}{key}") or os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in TRUE_VALUES


def read_optional_bool(key: str) -> bool | None:
    """설정된 경우에만 참 · 거짓을 돌려준다. **미설정(`None`)과 거짓을 섞지 않는다.**

    재무(`FINANCE_LLM_ENABLED` → `LLM_ENABLED` → 켬) · 판매(`SALES_LLM_ENABLED` →
    `LLM_ENABLED` → 끔)가 사슬을 직접 잇는다. 빈 문자열은 **거짓**이다(없는 것이 아니다).
    """
    value = os.getenv(key)
    if value is None:
        return None
    return value.strip().lower() in TRUE_VALUES


def int_env(prefix: str, key: str, default: str, *, minimum: int) -> int:
    """정수 설정. **파싱 실패는 기본값으로 되돌린다** — `.env` 오타 하나로 앱이 죽으면 안 된다."""
    try:
        return max(minimum, int(scoped_env(prefix, key, default)))
    except (TypeError, ValueError):
        return max(minimum, int(default))


def float_env(prefix: str, key: str, default: str, *, minimum: float) -> float:
    """실수 설정. 파싱 실패 시 기본값 — 이유는 `int_env` 와 같다."""
    try:
        return max(minimum, float(scoped_env(prefix, key, default)))
    except (TypeError, ValueError):
        return max(minimum, float(default))


def resolve_provider_model(
    prefix: str, *, default_provider: str, default_models: Mapping[str, str]
) -> tuple[str, str]:
    """`(provider, 모델)`. 모델은 앞뒤 공백을 떼지 않은 값이다(부르는 쪽이 뗀다).

    provider 는 `<PREFIX>LLM_PROVIDER` → `LLM_PROVIDER` → `default_provider` (소문자).

    🔴 **모델은 프로바이더에 종속된 값이다.** 부서가 전역과 **다른** 프로바이더를 쓸 때 전역
       `LLM_MODEL`(예: Ollama 의 `gemma3:4b`)을 물려받으면 Gemini 에 없는 모델을 요청해 400 · 404 가
       난다(물류 #95 · 마스터 실측). 그때만 전역 모델을 건너뛰고 기본값을 쓴다. 전용 모델이
       **직접 지정돼 있으면 그것이 이긴다.** 프로바이더가 같으면 전역 모델은 정당한 상속이다.

    ★ 마스터 · Critic · 물류가 글자까지 같게 쓰던 규칙이다. 매입(전역 모델을 그대로 상속) ·
      재무 · 판매(전역 provider 를 상속하지 않음)는 규칙이 달라 부서에 남겼다.
    """
    scoped_provider = os.getenv(f"{prefix}LLM_PROVIDER")
    global_provider = (os.getenv("LLM_PROVIDER") or default_provider).strip().lower()
    provider = (scoped_provider or global_provider).strip().lower()
    if provider != global_provider and not os.getenv(f"{prefix}LLM_MODEL"):
        model = default_models.get(provider, "")
    else:
        model = scoped_env(prefix, "LLM_MODEL", default_models.get(provider, ""))
    return provider, model


def gemini_api_key(prefix: str) -> str | None:
    """`<PREFIX>GEMINI_API_KEY` → `GEMINI_API_KEY`. **값을 설정 객체에 싣지 않는다** —
    설정 객체는 로그 · 예외에 통째로 실릴 수 있다. 부르는 쪽이 호출 직전에 읽는다.

    🔴 공용 키로 떨어지면 팀 공용 한도를 같이 쓴다 — 전용 키를 넣는 것이 기본이다.
    """
    return os.getenv(f"{prefix}GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY")


def run_with_fallback[Interpretation](
    *,
    enabled: bool,
    needs_call: bool,
    max_retries: int,
    call: Callable[[list[str] | None], str],
    validate: Callable[[str], Interpretation],
    template: Interpretation,
    guidance_for: Callable[[Exception], list[str] | None],
) -> tuple[Interpretation, str, int, bool]:
    """**재시도 · fallback 골격.** `(해석, 상태, 시도 수, fallback 썼나)` 를 돌려준다.

    상태는 봉투 계약의 네 값(`app/contracts/envelope.py` 의 `LLMStatus`) 가운데 하나다. core 는
    contracts 를 import 하지 않으므로 같은 문자열을 낸다 — 어긋나지 않는지는 검사가 본다.

    ```text
    enabled 거짓           → (template, DISABLED, 0, False)          부르지 않는다
    needs_call 거짓        → (template, SKIPPED_TEMPLATE, 0, False)  부르지 않는다
    call(안내) → validate  → (해석, SUCCESS, 시도, False)
    실패                   → guidance_for(오류) 가 다음 안내를 준다 → 최대 max_retries 번 더
    다 실패                → (template, FALLBACK, 시도, True)
    ```

    :param call: 안내(`None` 이면 첫 시도)를 받아 프로바이더에 묻고 **글자를 돌려준다.**
    :param guidance_for: 실패한 시도마다 **한 번** 불린다. `None` 을 돌려주면 더 묻지 않는다 —
        마스터가 전송 실패를 재시도하지 않을 때 쓴다(검증 실패만 다시 묻는다).

    🔴 **역할 로직이 여기 없다.** 무엇을 묻는지(`call`) · 무엇이 옳은지(`validate`) · 실패하면
       무엇으로 돌아갈지(`template`)는 전부 부르는 쪽이 준다.

    ⚠️ **모든 실패가 같은 자리로 떨어진다.** 키 없음 · 서버 없음 · 타임아웃 · SDK 예외를 전부
      받는다 — 키 · 서버 없이도 그래프가 도는 것이 이 한 줄에 걸려 있다.

    ★ 2026-09-30 BL-020: 매입 `run_with_fallback` 을 옮겼다. Critic `JudgeService` 의 루프가
      글자까지 같았고, 마스터 분류 · 응답 문장의 루프는 «전송 실패면 멈춤» 만 달라
      `guidance_for` 가 `None` 을 돌려주는 것으로 옮겼다.
    """
    if not enabled:
        return template, "DISABLED", 0, False
    if not needs_call:
        return template, "SKIPPED_TEMPLATE", 0, False

    guidance: list[str] | None = None
    attempts = 0
    for _ in range(max_retries + 1):
        attempts += 1
        try:
            return validate(call(guidance)), "SUCCESS", attempts, False
        except Exception as error:  # noqa: BLE001 - LLM 실패가 부르는 쪽을 멈추면 안 된다
            guidance = guidance_for(error)
            if guidance is None:
                break
    return template, "FALLBACK", attempts, True
