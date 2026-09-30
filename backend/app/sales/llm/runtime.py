"""Sales 후보 해석을 Gemini 구조화 출력으로 연결한다.

★ 요청 만들기 · 보내기 · 응답 읽기 · 스키마 낮추기는 `app.core.llm` 이 한다 (2026-09-30 재구성
  BL-020). 여기 남은 것은 판매의 몫이다 — 지시문 · 입출력 계약 · 검증 · `SALES_` 설정과 오류
  문장 · 한 번만 묻고 실패하면 템플릿으로 가는 규칙(재시도 없음).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.llm.providers import (
    first_text,
    gemini_json_request,
    gemini_parts,
    gemini_request,
    gemini_safe_schema,
    send_json,
)
from app.core.llm.runtime import ENV_FILES, gemini_api_key, load_env_files, read_optional_bool
from app.sales.schemas.proposal import SalesCandidate, SalesRecommendation
from app.sales.schemas.strategy import StrategyProfile

_NUMBER = re.compile(r"\d")
_DEFAULT_MODEL = "gemini-3.5-flash-lite"
_SYSTEM_PROMPT = """Sales 후보 중 하나만 추천하세요. 후보 ID 외 숫자·금액·수량·날짜를 쓰지 말고,
새 후보나 조건을 만들지 마세요. 모든 문장은 자연스러운 한국어로 작성하세요."""


_PLANNER_SYSTEM_PROMPT = """당신은 판매 전략 자세만 정합니다.
CONSERVATIVE · BALANCED · AGGRESSIVE 세 전략의 자세를 각각 한 번씩 고르세요.

주어진 재무·물류·ML 사실은 **판단 재료**입니다. 그 값을 답에 옮겨 적지 마세요.
가격·수량·금액·마진·판정은 결정론 코드가 계산하므로 절대 만들지 마세요.
reason_codes 에 숫자를 한 글자도 쓰지 말고, 주어진 어휘 밖의 값을 만들지 마세요.
DEPLETION 자세는 소진 신호가 실제로 있을 때만 고르세요."""


class StrategyPlanningInput(BaseModel):
    """Planner 가 보는 **사실**. 재무·물류·ML 이 실제로 보낸 값이다.

    ★ **숫자를 보여 준다. 숫자를 받지는 않는다.**

      ```text
      입력   현금·채무·채권·여신·재고·시장 밴드 — 판단에 필요하니 준다
      출력   닫힌 어휘의 자세뿐 — 숫자가 섞이면 계획을 통째로 버린다
      ```

      모델이 본 숫자가 가격이 될 길이 없다. 단가·수량·금액은 `domain/proposal.py` 의
      결정론 계산이 **자세만 읽고** 만들고, 모델 출력에는 숫자를 담을 칸이 없다.

    🔴 **판정 라벨을 주지 않는다.** `finance_verdict` 같은 값은 여기 없다 — 후보가
      아직 없으므로 판정도 없고, 있다 해도 모델이 판정을 따라 적을 자리를 만들지
      않는다.
    """

    model_config = ConfigDict(extra="forbid")

    #: 사용자
    business_mode: str | None = None
    #: 사용자가 말로 남긴 의도. **해석은 모델이 하고 숫자는 여기서 안 나온다** (§16).
    user_intent_text: str | None = None
    #: 물류
    depletion_pressure: bool
    freshness_risk_codes: list[str] = Field(default_factory=list)
    has_freshness_risk_lots: bool = False
    sell_priority: str | None = None
    inventory_risk_severity: str | None = None
    inventory_available_kg: float | None = None
    inventory_cost_basis_known: bool = False
    delivery_status: str | None = None
    #: 재무
    payment_pressure: str | None = None
    credit_state: Literal["AVAILABLE", "EXHAUSTED", "UNKNOWN"] = "UNKNOWN"
    finance_context_available: bool = False
    available_cash_krw: float | None = None
    base_projected_cash_min_krw: float | None = None
    minimum_cash_balance_krw: float | None = None
    payables_total_krw: float | None = None
    payables_due_7d_krw: float | None = None
    payables_due_30d_krw: float | None = None
    receivables_total_krw: float | None = None
    partner_receivable_krw: float | None = None
    partner_credit_available_krw: float | None = None
    #: ML
    ml_band_available: bool = False
    ml_target_kind: str | None = None
    ml_use_recommended: bool | None = None
    ml_lower: float | None = None
    ml_predicted: float | None = None
    ml_upper: float | None = None
    #: 되먹임 회차에서만 채워진다.
    feedback_reason_codes: list[str] = Field(default_factory=list)


class LlmStrategyProfileOutput(BaseModel):
    """모델이 돌려주는 자세 하나. **닫힌 어휘라 숫자가 들어올 칸이 없다.**"""

    model_config = ConfigDict(extra="forbid")

    strategy: Literal["CONSERVATIVE", "BALANCED", "AGGRESSIVE"]
    price_posture: Literal["MARGIN_DEFENSE", "MARKET_ALIGNED", "DEPLETION"]
    quantity_posture: Literal["LIMITED", "NORMAL", "EXPANDED"]
    inventory_posture: Literal["NORMAL", "FIFO", "FRESHNESS_RISK_FIRST"]
    credit_posture: Literal["STRICT", "NORMAL", "WITHIN_LIMIT"]
    cash_posture: Literal["DEFENSIVE", "NORMAL", "CASH_CONVERSION"]
    reason_codes: list[str] = Field(default_factory=list)


class LlmStrategyPlanOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategies: list[LlmStrategyProfileOutput]


@dataclass(frozen=True)
class StrategyPlanOutcome:
    """자세 셋과 **그것을 누가 만들었는가.** 실패를 숨기지 않는다 (§10)."""

    source: str
    llm_status: str
    profiles: list[Any]
    llm_provider: str | None = None
    llm_model: str | None = None
    #: 🔴 **왜 템플릿으로 떨어졌나.** 성공했으면 `None`.
    #:
    #:   `FALLBACK` 하나로는 **우리가 틀린 날과 저쪽이 막은 날**이 같아 보인다.
    #:   실제로 그 둘이 한 주에 다 일어났다 (2026-09-16).
    #:
    #:   ```text
    #:   HTTP_400   우리 스키마가 틀렸다        고칠 것이 여기 있다. 영구적이다
    #:   HTTP_429   저쪽이 쿼터로 막았다        고칠 것이 없다. 기다리면 풀린다
    #:   ```
    #:
    #: ★ **라벨이다.** 응답 본문을 넣지 않는다 — 거기에는 키·요청 내용이 섞일 수
    #:   있고, 이 값은 실행 이력에 그대로 남는다.
    failure_reason: str | None = None


def _failure_label(error: BaseException) -> str:
    """실패를 **한 라벨로** 줄인다. 본문도 URL 도 남기지 않는다.

    ★ 네 갈래면 충분하다 — 고칠 것이 우리에게 있나(계약·요청), 저쪽에 있나(상태
      코드), 아니면 길이 막혔나(연결).
    """
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP_{error.code}"
    if isinstance(error, urllib.error.URLError):
        return "PROVIDER_UNREACHABLE"
    if isinstance(error, (ValueError, TypeError)):
        # 계약 위반 — 모델이 어휘 밖을 냈거나 스키마를 못 폈다.
        return "CONTRACT_VIOLATION"
    return type(error).__name__


def plan_strategy_profiles(*, signals: Any, template: list[Any]) -> StrategyPlanOutcome:
    """후보를 만들기 **전에** 세 전략의 자세를 정한다.

    ```text
    설정 꺼짐          DISABLED   → 템플릿
    호출 실패·계약 위반 FALLBACK   → 템플릿 (+ 왜 떨어졌는지 라벨)
    성공              SUCCESS    → 모델 자세 (호출부가 사실로 한 번 더 깎는다)
    ```

    🔴 **템플릿으로 떨어져도 세 전략은 선다.** 외부 모델 하나 때문에 판매안이
      안 나오면, 그 모델이 없는 날 사업이 멈춘다.

    🔴 **왜 떨어졌는지를 같이 남긴다** (2026-09-16). 전에는 사유 없이 `FALLBACK` 만
      남겼고, 그래서 **우리 스키마 버그(`HTTP_400`)가 이 자리에 숨어 실환경에서
      Planner 가 한 번도 안 돈 채로 지나갔다** — 화면에는 *"모델이 실패했다"* 만
      보였고 그것은 쿼터가 막힌 날과 구별되지 않았다.
    """
    settings = load_settings()
    if not settings.enabled:
        return StrategyPlanOutcome("TEMPLATE_FALLBACK", "DISABLED", template, None, settings.model)
    try:
        output = _call_gemini_planner(_planner_context(signals), settings)
        profiles = _validated_profiles(output, template)
    except Exception as error:  # noqa: BLE001 - 외부 호출 실패는 판매안 실패가 아니다.
        return StrategyPlanOutcome(
            "TEMPLATE_FALLBACK",
            "FALLBACK",
            template,
            settings.provider,
            settings.model,
            failure_reason=_failure_label(error),
        )
    return StrategyPlanOutcome("LLM", "SUCCESS", profiles, settings.provider, settings.model)


def _planner_context(signals: Any) -> StrategyPlanningInput:
    """모델에 나가는 것 전부. **여기 없는 것은 모델이 못 본다.**"""
    if signals.credit_available_krw is None:
        credit_state = "UNKNOWN"
    else:
        credit_state = "EXHAUSTED" if signals.credit_available_krw <= 0 else "AVAILABLE"

    def num(value: Any) -> float | None:
        return None if value is None else float(value)

    return StrategyPlanningInput(
        business_mode=signals.business_mode,
        user_intent_text=signals.user_intent_text,
        depletion_pressure=signals.depletion_pressure,
        freshness_risk_codes=list(signals.freshness_risk_codes),
        has_freshness_risk_lots=bool(signals.item_lot_ids) and bool(signals.freshness_risk_codes),
        sell_priority=signals.sell_priority,
        inventory_risk_severity=signals.inventory_risk_severity,
        inventory_available_kg=num(signals.inventory_available_kg),
        inventory_cost_basis_known=signals.inventory_cost_basis_known,
        delivery_status=signals.delivery_status,
        payment_pressure=signals.payment_pressure,
        credit_state=credit_state,
        finance_context_available=signals.has_finance_context,
        available_cash_krw=num(signals.available_cash_krw),
        base_projected_cash_min_krw=num(signals.base_projected_cash_min_krw),
        minimum_cash_balance_krw=num(signals.minimum_cash_balance_krw),
        payables_total_krw=num(signals.payables_total_krw),
        payables_due_7d_krw=num(signals.payables_due_7d_krw),
        payables_due_30d_krw=num(signals.payables_due_30d_krw),
        receivables_total_krw=num(signals.receivables_total_krw),
        partner_receivable_krw=num(signals.partner_receivable_krw),
        partner_credit_available_krw=num(signals.credit_available_krw),
        ml_band_available=signals.ml_gate_open,
        ml_target_kind=signals.ml_target_kind,
        ml_use_recommended=signals.ml_use_recommended,
        ml_lower=num(signals.ml_lower),
        ml_predicted=num(signals.ml_predicted),
        ml_upper=num(signals.ml_upper),
        feedback_reason_codes=list(signals.feedback_reason_codes),
    )


def _call_gemini_planner(context: StrategyPlanningInput, settings: LLMSettings):
    """해석 호출과 **같은 전선, 다른 계약**이다 — 스키마와 지시문만 다르다."""
    return _gemini_structured(
        system_prompt=_PLANNER_SYSTEM_PROMPT,
        user_json=json.dumps(context.model_dump(), ensure_ascii=False),
        schema_model=LlmStrategyPlanOutput,
        settings=settings,
    )


def _validated_profiles(output: LlmStrategyPlanOutput, template: list[Any]) -> list[Any]:
    """모델 자세를 받아들일지 정한다. **어긋나면 통째로 버린다.**

    ★ 일부만 고쳐 쓰지 않는다. 세 전략 중 하나가 빠졌거나 두 번 왔으면 모델이 계약을
      이해하지 못한 것이고, 그런 계획에서 한 줄만 건져 쓰면 **어디까지가 모델의
      판단인지** 아무도 말할 수 없다.

    🔴 **사유에 숫자가 있으면 버린다.** 자세는 라벨이고, 라벨에 숫자가 섞이는 순간
      모델이 값을 말하기 시작한 것이다.
    """
    names = [item.strategy for item in output.strategies]
    if sorted(names) != ["AGGRESSIVE", "BALANCED", "CONSERVATIVE"]:
        raise ValueError("strategy set is not A/B/C exactly once")
    for item in output.strategies:
        if any(_NUMBER.search(code) for code in item.reason_codes):
            raise ValueError("unsafe planner output")
    del template
    return [StrategyProfile.model_validate(item.model_dump()) for item in output.strategies]


class CandidateInterpretationInput(BaseModel):
    """LLM은 식별자와 의미 라벨만 받아 숫자를 바꿀 수 없다."""

    model_config = ConfigDict(extra="forbid")
    candidate_id: str
    strategy_label: str | None = None
    adjustment_axis: str
    conditional: bool
    risk_labels: list[str] = Field(default_factory=list)
    uncertainty_labels: list[str] = Field(default_factory=list)


class LlmInterpretationOutput(BaseModel):
    """숫자 필드가 없는 LLM 해석 결과."""

    model_config = ConfigDict(extra="forbid")
    recommended_candidate_id: str
    summary: str
    recommendation_reason: str
    risk_explanation: str
    user_message: str


@dataclass(frozen=True)
class LLMSettings:
    enabled: bool
    provider: str
    model: str
    timeout_seconds: float


def load_settings() -> LLMSettings:
    """Sales 전용 설정을 우선하고 전역 Ollama 설정이 모델로 섞이지 않게 한다.

    ⚠️ 켜짐 기본값은 **꺼짐**이다(`SALES_LLM_ENABLED` → `LLM_ENABLED` → 끔 · 빈 값은 끔) —
      다른 부서(기본 켬)와 다르다. provider 는 `SALES_LLM_PROVIDER` 만 보고(기본 gemini), timeout 은
      공용 `LLM_TIMEOUT_SECONDS` 만 본다(잘못된 값이면 예외). 옮기기 전 그대로다.
    """
    _load_environment()
    enabled = read_optional_bool("SALES_LLM_ENABLED")
    if enabled is None:
        enabled = read_optional_bool("LLM_ENABLED")
    provider = (os.getenv("SALES_LLM_PROVIDER") or "gemini").strip().lower()
    explicit_model = os.getenv("SALES_LLM_MODEL")
    common_provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    common_model = os.getenv("LLM_MODEL")
    # 공통 Provider가 다르면 다른 Agent의 모델명을 Sales에 물려주지 않는다.
    inherited_model = (
        common_model if provider == common_provider and common_model else _DEFAULT_MODEL
    )
    model = explicit_model or inherited_model
    return LLMSettings(
        enabled=False if enabled is None else enabled,
        provider=provider,
        model=model,
        timeout_seconds=max(0.1, float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))),
    )


def interpret_candidates(
    candidates: list[SalesCandidate], *, recommended_candidate_id: str | None = None
) -> SalesRecommendation:
    """Gemini 실패는 Scenario를 바꾸지 않고 결정론 fallback으로만 전환한다."""
    settings = load_settings()
    if not candidates:
        return _fallback(candidates, "SKIPPED_TEMPLATE", settings, 0, recommended_candidate_id)
    if not settings.enabled:
        return _fallback(candidates, "DISABLED", settings, 0, recommended_candidate_id)
    try:
        output = _call_gemini(_safe_context(candidates), settings)
        return _validated(candidates, output, settings, recommended_candidate_id)
    except Exception:  # noqa: BLE001 - 외부 호출 실패는 Sales 제안 실패가 아니다.
        return _fallback(candidates, "FALLBACK", settings, 1, recommended_candidate_id)


def _safe_context(candidates: list[SalesCandidate]) -> list[CandidateInterpretationInput]:
    """LLM에는 수량·가격·날짜를 전달하지 않고 의미 라벨만 전달한다."""
    return [
        CandidateInterpretationInput(
            candidate_id=c.candidate_id,
            strategy_label=c.strategy_label,
            adjustment_axis=c.adjustment_axis,
            conditional=c.conditional,
            risk_labels=c.risks,
            uncertainty_labels=c.uncertainties,
        )
        for c in candidates
    ]


def _call_gemini(context: list[CandidateInterpretationInput], settings: LLMSettings):
    """Gemini의 JSON Schema 응답을 받아 Sales 전용 계약으로 검증한다."""
    return _gemini_structured(
        system_prompt=_SYSTEM_PROMPT,
        user_json=json.dumps([c.model_dump() for c in context], ensure_ascii=False),
        schema_model=LlmInterpretationOutput,
        settings=settings,
    )


def _gemini_structured(
    *, system_prompt: str, user_json: str, schema_model: type[BaseModel], settings: LLMSettings
):
    """구조화 출력 한 번. **두 호출(해석·전략)이 같은 전선을 쓴다.**

    ★ 전선을 두 벌로 두면 타임아웃·키·스키마 낮추기가 두 곳에서 갈린다 — 한쪽만
      고치는 날이 오고, 그날 한쪽 호출만 조용히 다른 규칙으로 돈다.
    ★ 전송 예외(`HTTPError` · `URLError` · 깨진 JSON)를 감싸지 않는다 — 실패 라벨
      (`_failure_label`)이 종류로 가른다. 주소는 기본 주소 하나다(환경변수로 바꾸지 않는다).
    ★ 스키마는 `gemini_safe_schema` 로 낮춘다 — 중첩 모델의 `$defs` · `$ref` 를 펴 넣는다.
    """
    if settings.provider != "gemini":
        raise RuntimeError("unsupported Sales LLM provider")
    api_key = gemini_api_key("SALES_")
    if not api_key:
        raise RuntimeError("Sales Gemini API key is not set")
    payload = gemini_json_request(
        system_prompt, user_json, gemini_safe_schema(schema_model.model_json_schema())
    )
    request = gemini_request(settings.model, payload, api_key=api_key)
    document = send_json(request, timeout=settings.timeout_seconds)
    return schema_model.model_validate_json(_gemini_response_text(document))


def _gemini_response_text(document: dict[str, Any]) -> str:
    """공백이 아닌 첫 글자 조각을 앞뒤 공백을 떼어 돌려준다. 없으면 `ValueError`.

    ⚠️ 사고(`thought`) 조각을 따로 건너뛰지 않는다 — 마스터 · Critic · 재무와 다르다
      (2026-09-30 BL-020 확인 — 바꾸지 않았다).
    """
    text = first_text(gemini_parts(document))
    if text is None:
        raise ValueError("empty Gemini response")
    return text.strip()


def _validated(candidates, output, settings, fixed_recommendation=None) -> SalesRecommendation:
    """후보 ID·빈 문장·숫자 포함 여부를 검사해 LLM의 권한을 제한한다."""
    selectable = {c.candidate_id for c in candidates if "FINANCE_FAIL" not in c.risks}
    if fixed_recommendation is None and output.recommended_candidate_id not in selectable:
        raise ValueError("unknown candidate")
    if fixed_recommendation is not None and fixed_recommendation not in selectable:
        raise ValueError("deterministic recommendation is not selectable")
    texts = (
        output.summary,
        output.recommendation_reason,
        output.risk_explanation,
        output.user_message,
    )
    if any(not text.strip() or _NUMBER.search(text) for text in texts):
        raise ValueError("unsafe LLM output")
    return SalesRecommendation(
        status="SUCCESS",
        recommended_candidate_id=(fixed_recommendation or output.recommended_candidate_id),
        summary=output.summary,
        recommendation_reason=output.recommendation_reason,
        risk_explanation=output.risk_explanation,
        user_message=output.user_message,
        llm_provider="gemini",
        llm_model=settings.model,
        llm_attempts=1,
        llm_fallback_used=False,
    )


def _fallback(
    candidates, status, settings, attempts, fixed_recommendation=None
) -> SalesRecommendation:
    selectable = [c for c in candidates if "FINANCE_FAIL" not in c.risks]
    candidate = next((c for c in selectable if not c.conditional), None)
    if candidate is None and selectable:
        candidate = selectable[0]
    return SalesRecommendation(
        status=status,
        recommended_candidate_id=(
            fixed_recommendation
            if fixed_recommendation in {item.candidate_id for item in selectable}
            else candidate.candidate_id
            if candidate
            else None
        ),
        summary="규칙 기반 판매안을 준비했습니다.",
        recommendation_reason="외부 해석 없이 근거가 있는 판매 조건을 우선 표시합니다.",
        risk_explanation="외부 검증 결과와 조건부 조달 여부를 함께 확인해 주세요.",
        user_message="현재 확인된 조건을 기준으로 판매안을 검토해 주세요.",
        llm_provider=settings.provider if settings.enabled else None,
        llm_model=settings.model,
        llm_attempts=attempts,
        llm_fallback_used=status == "FALLBACK",
    )


def _load_environment() -> None:
    load_env_files(ENV_FILES, override=False)
