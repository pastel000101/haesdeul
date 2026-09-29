"""판매 전략 자세의 어휘와 계획 — **숫자가 한 칸도 없다.**

★ 2026-09-29 BL-013: `sales/strategy.py` 에서 모델과 어휘를 옮겼다. 사실을 읽고 자세를
  깎는 규칙은 `domain/strategy.py`, 모델을 불러 계획을 세우는 순서는 `service/strategy.py`,
  모델 호출 자체는 `llm/runtime.py` 다 — 전에는 `strategy ⇄ llm.runtime` 이 서로를 함수 안에서
  import 했다. 자세를 고르는 사실(`StrategySignals`)은 판정 속성을 가져
  `domain/strategy.py` 에 둔다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

StrategyName = Literal["CONSERVATIVE", "BALANCED", "AGGRESSIVE"]

PricePosture = Literal["MARGIN_DEFENSE", "MARKET_ALIGNED", "DEPLETION"]
"""가격을 **어느 기준에 붙일 것인가.** 값이 아니라 기준이다.

```text
MARGIN_DEFENSE   시장 상단과 마진 경고선 중 높은 쪽
MARKET_ALIGNED   시장 예측치와 마진 최저선 중 높은 쪽
DEPLETION        시장 하단까지 열되 마진 최저선 아래로는 안 간다
```
"""

QuantityPosture = Literal["LIMITED", "NORMAL", "EXPANDED"]
InventoryPosture = Literal["NORMAL", "FIFO", "FRESHNESS_RISK_FIRST"]
CreditPosture = Literal["STRICT", "NORMAL", "WITHIN_LIMIT"]
CashPosture = Literal["DEFENSIVE", "NORMAL", "CASH_CONVERSION"]

#: 물류가 이미 쓰고 있는 신선도 위험 코드. **새로 만들지 않는다.**
#:
#: 🔴 `STORAGE_TARGET_EXCEEDED` 를 여기 넣지 않는다. 보관 목표를 넘겼다는 것은
#:   *"많이 쌓였다"* 이지 *"상한다"* 가 아니다 — 물류가 그 둘을 다른 코드로 낸다.
FRESHNESS_RISK_CODE = "FRESHNESS_QUALITY_RISK"


class StrategyProfile(BaseModel):
    """A/B/C 하나의 **자세**. 숫자가 한 칸도 없다 — 그것이 계약이다."""

    model_config = ConfigDict(extra="forbid")

    strategy: StrategyName
    price_posture: PricePosture
    quantity_posture: QuantityPosture
    inventory_posture: InventoryPosture
    credit_posture: CreditPosture
    cash_posture: CashPosture
    #: 왜 이 자세인가. **닫힌 어휘가 아니다** — 사람이 읽는 사유이고 판정에 안 쓴다.
    reason_codes: list[str] = Field(default_factory=list)


class StrategyPlan(BaseModel):
    """세 자세와 **그것을 누가 만들었는가.**

    🔴 **출처를 숨기지 않는다** (§10). 모델이 실패했는데 성공처럼 보이면, 모델이
      죽은 날과 산 날이 화면에서 같아진다.
    """

    model_config = ConfigDict(extra="forbid")

    source: Literal["LLM", "TEMPLATE_FALLBACK"]
    llm_status: Literal["SUCCESS", "SKIPPED_TEMPLATE", "FALLBACK", "DISABLED"]
    profiles: list[StrategyProfile]
    llm_provider: str | None = None
    llm_model: str | None = None
    #: 모델이 고른 자세를 사실이 내린 자리. **내렸다는 사실 자체를 남긴다.**
    clamped_reason_codes: list[str] = Field(default_factory=list)
    #: 🔴 **왜 템플릿으로 떨어졌나.** 성공했거나 설정이 꺼진 날은 `None`.
    #:
    #:   `HTTP_400` 은 우리가 고칠 것이 있다는 뜻이고 `HTTP_429` 는 기다리면
    #:   풀린다는 뜻이다 — 사유가 없으면 그 둘이 화면에서 같아진다.
    llm_failure_reason: str | None = None

    def of(self, strategy: str) -> StrategyProfile | None:
        return next((p for p in self.profiles if p.strategy == strategy), None)
