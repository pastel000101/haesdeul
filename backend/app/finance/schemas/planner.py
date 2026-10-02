"""재무 Planner 계약 — Controller · Harness · Planner 가 함께 쓰는 모양과 표.

```text
FINALIZE_TOOL_NAME          종료 Tool 의 이름 (종료도 Tool 호출이다)
CAPABILITY_OWNER            capability → 그것을 유일하게 채우는 Tool
ToolAction                  Planner 가 고른 행동 하나
FinancePlanner · FinanceFinalizer          Planner · Finalizer 가 지킬 모양 (Protocol)
FinancePlannerFailure · …Unavailable · …ContractViolation   Controller 로 올라가는 실패 셋
```

Planner(`llm/`)는 Harness(`service/`)의 표를, Harness 는 Planner 의 예외를 쓴다. 서로를
직접 들이면 순환이 되므로 셋이 함께 쓰는 계약을 아래층(`schemas/`)에 두어 순환 없이 맨 위에서
들인다. 선행 의존 · 필수 capability · 승인은 Harness(`service/harness.py`) 몫이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.contracts.core import Evidence
from app.contracts.envelope import AgentRequest
from app.finance.schemas.agent import FinanceMode

#: capability 를 다 채웠을 때 Planner 가 부르는 종료 Tool 의 이름.
#:
#: 종료도 Tool 호출이다. 자유 문장으로 "재무 검토 완료" 라고 답할 자리를 주지 않기
#: 위해서다 — 필수 capability 가 남아 있으면 Harness 가 이 Tool 을 아예 바인딩하지 않는다.
#: 이름을 여기서 소유하는 이유는 Planner 계약이기 때문이다.
FINALIZE_TOOL_NAME = "finalize_finance_review"


#: capability → 그 capability 를 유일하게 채우는 Tool.
CAPABILITY_OWNER: dict[str, str] = {
    "finance_position": "assess_finance_position",
    "cashflow_projection": "project_cashflow",
    "finance_cap": "calculate_purchase_finance_cap",
    "payment_pressure": "analyze_payment_pressure",
    "scenario_evaluation": "evaluate_purchase_scenario",
    "amount_adjustment_validation": "validate_amount_adjustment",
    "sales_scenario_evaluation": "evaluate_sales_scenario",
}


@dataclass(frozen=True)
class ToolAction:
    tool_name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    finalize: bool = False


class FinancePlanner(Protocol):
    model: str
    attempts: int

    def decide(
        self,
        *,
        request: AgentRequest,
        allowed_tools: frozenset[str],
        observations: tuple[dict[str, Any], ...],
        missing_capabilities: tuple[str, ...],
        **kwargs: Any,
    ) -> ToolAction: ...


class FinanceFinalizer(Protocol):
    model: str
    attempts: int

    def finalize(
        self,
        *,
        mode: FinanceMode,
        business_status: str,
        evidences: tuple[Evidence, ...],
        has_verified_adjustment: bool = False,
    ) -> str: ...


class FinancePlannerFailure(RuntimeError):
    """되돌릴 수 없는 Planner 실패를 Controller 상태로 전달한다.

    Provider 장애·네트워크 오류·구조화 출력 파싱 불가처럼 다시 물어도 같은 것이
    여기로 온다. 모델이 계약을 어긴 것은 `FinancePlannerContractViolation` 이다.
    """


class FinancePlannerUnavailable(FinancePlannerFailure):
    """구성된 LLM Planner들이 모두 실행 불가해 결정론 선택으로 내릴 수 있는 실패."""

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.reason = reason


class FinancePlannerContractViolation(ValueError):
    """모델이 계약을 어긴 회복 가능한 잘못.

    이것을 `FinancePlannerFailure` 와 섞으면 재계획이 죽는다. 검증 실패가 `decide()` 안에서
    실패 예외로 올라오면 Controller 가 통째로 ERROR 로 접어, `service/harness.py` 의
    `guard_replan` 이 쓰일 자리가 없고 `metadata.replans` 는 늘 0 이 된다. 허용되지 않은 Tool
    선택 같은 잘못은 왜 반려됐는지 알려주고 다시 묻는다.
    """
