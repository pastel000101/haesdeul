"""판매 전략 계획 — 사실을 모으고, 모델에 자세를 묻고, 사실로 깎는다.

규칙(사실 수집 · 템플릿 · 깎기)은 `domain/strategy.py`, 모델 호출은
`llm/runtime.py::plan_strategy_profiles`, 자세 모델은 `schemas/strategy.py` 다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.sales.domain.strategy import (
    StrategySignals,
    clamp_profiles,
    derive_signals,
    template_profiles,
)
from app.sales.llm.runtime import plan_strategy_profiles
from app.sales.schemas.strategy import StrategyPlan


def plan_strategies(
    request: Any, replies: Sequence[Any] = ()
) -> tuple[StrategyPlan, StrategySignals]:
    """세 전략의 자세를 정한다. 모델이 실패해도 세 자세는 선다.

    모델 호출은 `app.sales.llm.runtime` 이 소유한다 — 이 파일은 무엇을 물을지와
    무엇을 받아들일지만 정한다.
    """
    signals = derive_signals(request, replies)
    template = template_profiles(signals)
    outcome = plan_strategy_profiles(signals=signals, template=template)
    profiles, notes = clamp_profiles(outcome.profiles, signals)
    return (
        StrategyPlan(
            source=outcome.source,
            llm_status=outcome.llm_status,
            profiles=profiles,
            llm_provider=outcome.llm_provider,
            llm_model=outcome.llm_model,
            clamped_reason_codes=notes,
            llm_failure_reason=outcome.failure_reason,
        ),
        signals,
    )
