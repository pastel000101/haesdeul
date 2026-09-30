"""`app/core/llm/runtime.py` — 실행 골격과 `<PREFIX>_` 우선 설정 읽기 (2026-09-30 재구성 BL-020).

★ 골격이 내는 상태는 봉투 계약(`app/contracts/envelope.py` 의 `LLMStatus`)의 네 값이어야 한다 —
  core 는 contracts 를 import 하지 않고 같은 문자열을 내므로, 어긋나지 않는지를 여기서 잰다.
★ 부서마다 다른 읽기 규칙(빈 값의 뜻 · 기본값 · 잘못된 값)이 옮기기 전과 같은지는 전후 탐침과 부서
  검사가 본다. 여기서는 core 가 약속한 규칙을 잠근다.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.contracts.envelope import LLM_STATUSES
from app.core.llm import runtime
from app.core.llm.runtime import (
    TRUE_VALUES,
    float_env,
    gemini_api_key,
    int_env,
    load_env_files,
    read_bool,
    read_optional_bool,
    resolve_provider_model,
    run_with_fallback,
    scoped_env,
)


class _Invalid(ValueError):
    pass


def _run(outcomes: list[Any], *, enabled=True, needs_call=True, max_retries=1, stop_on=()):
    """`call` 이 부를 때마다 다음 결과를 낸다(예외면 던진다). 받은 안내 · 부른 횟수를 적는다."""
    seen: dict[str, list] = {"guidance": [], "failures": []}
    queue = list(outcomes)

    def call(guidance):
        seen["guidance"].append(guidance)
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def validate(raw: str) -> str:
        if raw == "bad":
            raise _Invalid("bad")
        return f"ok:{raw}"

    def guidance_for(error: Exception):
        seen["failures"].append(type(error).__name__)
        if isinstance(error, stop_on):
            return None
        return [f"fix {type(error).__name__}"]

    result = run_with_fallback(
        enabled=enabled,
        needs_call=needs_call,
        max_retries=max_retries,
        call=call,
        validate=validate,
        template="template",
        guidance_for=guidance_for,
    )
    return result, seen


def test_disabled_and_skipped_never_call():
    assert _run([], enabled=False)[0] == ("template", "DISABLED", 0, False)
    assert _run([], enabled=False, needs_call=False)[0] == ("template", "DISABLED", 0, False)
    assert _run([], needs_call=False)[0] == ("template", "SKIPPED_TEMPLATE", 0, False)


def test_success_on_the_first_call():
    result, seen = _run(["x"])
    assert result == ("ok:x", "SUCCESS", 1, False)
    assert seen["guidance"] == [None]


def test_a_failure_is_retried_with_its_guidance():
    result, seen = _run(["bad", "y"])
    assert result == ("ok:y", "SUCCESS", 2, False)
    assert seen["guidance"] == [None, ["fix _Invalid"]]
    assert seen["failures"] == ["_Invalid"]


def test_exhausted_retries_fall_back_after_max_retries_plus_one_calls():
    """🔴 호출 수는 `max_retries + 1` 이다 — 프로바이더도 SDK 도 따로 재시도하지 않는다."""
    for max_retries in (0, 1, 3):
        result, seen = _run([RuntimeError("down")] * (max_retries + 1), max_retries=max_retries)
        assert result == ("template", "FALLBACK", max_retries + 1, True)
        assert len(seen["guidance"]) == max_retries + 1
        assert len(seen["failures"]) == max_retries + 1


def test_none_guidance_stops_retrying():
    """마스터 분류 · 응답 문장: 검증 실패만 다시 묻고 전송 실패는 곧바로 되돌아간다."""
    result, seen = _run([RuntimeError("down"), "never"], stop_on=(RuntimeError,))
    assert result == ("template", "FALLBACK", 1, True)
    assert seen["guidance"] == [None]

    result, seen = _run(["bad", RuntimeError("down")], stop_on=(RuntimeError,))
    assert result == ("template", "FALLBACK", 2, True)
    assert seen["failures"] == ["_Invalid", "RuntimeError"]


def test_every_status_is_an_envelope_status():
    statuses = {
        _run([], enabled=False)[0][1],
        _run([], needs_call=False)[0][1],
        _run(["x"])[0][1],
        _run(["bad", "bad"])[0][1],
    }
    assert statuses == set(LLM_STATUSES)


# ── 설정 읽기 ───────────────────────────────────────────────────────────────


def _env(monkeypatch, **values: str | None) -> None:
    for key, value in values.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def test_scoped_env_skips_empty_values(monkeypatch):
    _env(monkeypatch, P_K=None, K=None)
    assert scoped_env("P_", "K", "d") == "d"
    _env(monkeypatch, P_K="", K="g")
    assert scoped_env("P_", "K", "d") == "g"
    _env(monkeypatch, P_K="s", K="g")
    assert scoped_env("P_", "K", "d") == "s"


@pytest.mark.parametrize(
    ("scoped", "shared", "default", "expected"),
    [
        (None, None, True, True),
        (None, None, False, False),
        (" YES ", "false", False, True),
        ("0", "on", True, False),
        ("", "on", False, True),
        (None, "", True, False),
        ("", "", True, False),
        ("garbage", None, True, False),
    ],
)
def test_read_bool(monkeypatch, scoped, shared, default, expected):
    _env(monkeypatch, P_FLAG=scoped, FLAG=shared)
    assert read_bool("FLAG", prefix="P_", default=default) is expected


@pytest.mark.parametrize(
    ("value", "expected"), [(None, None), ("", False), (" On ", True), ("0", False)]
)
def test_read_optional_bool_keeps_unset_apart_from_false(monkeypatch, value, expected):
    _env(monkeypatch, FLAG=value)
    assert read_optional_bool("FLAG") is expected


def test_truthy_values():
    assert frozenset({"1", "true", "yes", "on"}) == TRUE_VALUES


def test_number_settings_fall_back_to_the_default_on_bad_values(monkeypatch):
    _env(monkeypatch, P_N="abc", N="7")
    assert int_env("P_", "N", "3", minimum=0) == 3
    _env(monkeypatch, P_N="", N="7")
    assert int_env("P_", "N", "3", minimum=0) == 7
    _env(monkeypatch, P_N="-5", N=None)
    assert int_env("P_", "N", "3", minimum=0) == 0
    _env(monkeypatch, P_T="x", T=None)
    assert float_env("P_", "T", "30", minimum=0.1) == 30.0
    _env(monkeypatch, P_T="0", T=None)
    assert float_env("P_", "T", "30", minimum=0.1) == 0.1


@pytest.mark.parametrize(
    ("scoped_provider", "global_provider", "scoped_model", "global_model", "expected"),
    [
        (None, None, None, None, ("ollama", "gemma")),
        (None, None, None, "g-model", ("ollama", "g-model")),
        ("gemini", "ollama", None, "g-model", ("gemini", "flash")),
        ("gemini", "ollama", "s-model", "g-model", ("gemini", "s-model")),
        ("gemini", "ollama", "", "g-model", ("gemini", "flash")),
        (" Gemini ", "gemini", None, "g-model", ("gemini", "g-model")),
        ("bogus", None, None, "g-model", ("bogus", "")),
        ("", "gemini", None, None, ("gemini", "flash")),
    ],
)
def test_resolve_provider_model(
    monkeypatch, scoped_provider, global_provider, scoped_model, global_model, expected
):
    """전역과 **다른** provider 일 때만 전역 모델을 건너뛴다 — 전용 모델이 있으면 그것이 이긴다."""
    _env(
        monkeypatch,
        P_LLM_PROVIDER=scoped_provider,
        LLM_PROVIDER=global_provider,
        P_LLM_MODEL=scoped_model,
        LLM_MODEL=global_model,
    )
    defaults = {"ollama": "gemma", "gemini": "flash"}
    assert (
        resolve_provider_model("P_", default_provider="ollama", default_models=defaults) == expected
    )


def test_gemini_api_key_prefers_the_scoped_key(monkeypatch):
    _env(monkeypatch, P_GEMINI_API_KEY=None, GEMINI_API_KEY=None)
    assert gemini_api_key("P_") is None
    _env(monkeypatch, P_GEMINI_API_KEY="", GEMINI_API_KEY="shared")
    assert gemini_api_key("P_") == "shared"
    _env(monkeypatch, P_GEMINI_API_KEY="scoped", GEMINI_API_KEY="shared")
    assert gemini_api_key("P_") == "scoped"


def test_load_env_files_loads_in_order_without_overriding(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(
        runtime, "load_dotenv", lambda path, override: seen.append((path, override))
    )
    first, second = tmp_path / "a.env", tmp_path / "b.env"
    load_env_files((first, second))
    assert seen == [(first, False), (second, False)]


def test_env_files_are_backend_then_repository_root():
    from app.core.settings import ENV_FILE

    assert runtime.ENV_FILES == (ENV_FILE, ENV_FILE.parent.parent / ".env")
