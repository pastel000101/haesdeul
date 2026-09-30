"""에이전트 실행 상태에서 읽는 판단 — 마지막 시나리오 판정.

★ 2026-09-29 재구성 BL-014: `finance/state.py` 의 `_scenario_verdict` 를 공개 이름으로 옮겼다(몸통
  그대로).
"""

from __future__ import annotations

from app.finance.schemas.agent_state import FinanceAgentState


def latest_scenario_verdict(state: FinanceAgentState) -> str | None:
    return next(
        (
            observation["result"]["verdict"]
            for observation in reversed(state.observations)
            if "verdict" in observation.get("result", {})
        ),
        None,
    )
