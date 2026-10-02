"""한 Finance 실행 동안 살아 있는 값.

여기 없는 것: capability 소유·의존 같은 정적 계약(`schemas/planner.py` 의 `CAPABILITY_OWNER`,
`service/harness.py` 의 `TOOL_DEPENDENCIES`)과, 그것을 실행 시점에 강제하는 통제
(`service/harness.py`).

`missing_source_name`(빠진 정책 출처를 부르는 이름)은 상태가 쓰는 이름 규약이라 여기 둔다 —
모델이 판단 모듈을 import 하지 않게. 마지막 판정을 찾는 `latest_scenario_verdict` 는
`domain/agent_state.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.contracts.envelope import AgentRequest
from app.finance.schemas.agent import CashEvent, FinancePolicy


def missing_source_name(key: str) -> str:
    """근거 없이 뺀 정책값을 `missing_data` 에 적을 때 쓰는 이름.

    한 이름으로만 부른다. 어댑터 경계와 Tool 이 서로 다른 이름으로 적으면, 같은 사실이 두
    이름으로 이력에 남아 나중에 세어 볼 수 없다.
    """
    return f"{key}@policy_source_ref"


@dataclass(frozen=True)
class ScenarioPayment:
    seq: int
    purchase_date: date
    payment_date: date
    qty_kg: Decimal | None
    amount_krw: Decimal
    amount_max_krw: Decimal
    basis: str


@dataclass
class FinanceAgentState:
    request: AgentRequest
    branch_id: str = "PRE_PURCHASE"
    observations: list[dict[str, Any]] = field(default_factory=list)
    tool_order: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    replans: int = 0
    context_cache: tuple[dict[str, Any], FinancePolicy, list[CashEvent]] | None = None
    projection: Any = None
    scenario_projection: Any = None
    scenario_cap: Decimal | None = None
    scenario_schedule: tuple[ScenarioPayment, ...] = ()
    base_state_violated: bool = False
    missing_sources: list[str] = field(default_factory=list)
    #: Harness 가 남기는 실행 흔적. 관측이지 업무 결과가 아니다 — 회신
    #: payload 로 올라가지 않고 실행 metadata 로만 나간다.
    trace: list[dict[str, Any]] = field(default_factory=list)

    def note_missing_source(self, key: str) -> None:
        """근거를 달지 못해 뺀 정책값을 기록한다. 어댑터 경계와 같은 이름을 쓴다."""
        self.note_missing_source_name(missing_source_name(key))

    def note_missing_source_name(self, name: str) -> None:
        """이미 이름이 정해진 항목을 담는다. 같은 것을 두 번 적지 않는다."""
        if name not in self.missing_sources:
            self.missing_sources.append(name)
