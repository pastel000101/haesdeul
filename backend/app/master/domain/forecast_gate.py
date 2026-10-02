"""예측 관문의 판정 모델과 접는 규칙 — 품목별 답을 하루 하나로 접는다. 적재는
  `readmodel/forecast_gate.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

#: 품목 하나의 답.
Readiness = Literal["READY", "NOT_YET", "UNREADABLE"]

#: 날 하나의 답.
DayReadiness = Literal["ALL_READY", "SOME_READY", "NONE_READY", "UNREADABLE"]


@dataclass(frozen=True)
class ItemForecastGate:
    """품목 하나의 게이트 답. 등급을 지우지 않고 같이 들고 다닌다.

    `grade` 를 남기는 이유는 `readiness` 가 접은 값이기 때문이다. 접힌 값만 남으면
      "왜 그렇게 접혔는가" 를 다시 물을 데가 없고, 그때 사람은 새 쿼리를 짠다.
    """

    item: str
    as_of: date
    readiness: Readiness
    #: `load_forecast` 가 낸 등급. 못 물었으면 `None` — 등급이 없었다는 뜻이다.
    grade: str | None
    #: 사람이 읽는 사유. `load_forecast` 의 `note` 를 그대로 옮긴다.
    reason: str = ""

    @property
    def ready(self) -> bool:
        return self.readiness == "READY"


@dataclass(frozen=True)
class DayForecastReadiness:
    """날 하나로 접은 답. 품목별 내역을 반드시 같이 담는다.

    `SOME_READY` 를 `ALL_READY` 나 `NONE_READY` 로 접으면 "배추만 왔는데 전부 왔다" 로
    읽히거나 그 반대가 된다. 그래서 `items` 가 비어 있을 수 없다.
    """

    as_of: date
    readiness: DayReadiness
    items: tuple[ItemForecastGate, ...]

    def _of(self, readiness: Readiness) -> tuple[str, ...]:
        return tuple(gate.item for gate in self.items if gate.readiness == readiness)

    @property
    def ready_items(self) -> tuple[str, ...]:
        """예측이 온 품목."""
        return self._of("READY")

    @property
    def not_yet_items(self) -> tuple[str, ...]:
        """확인했고 아직 안 온 품목."""
        return self._of("NOT_YET")

    @property
    def unreadable_items(self) -> tuple[str, ...]:
        """못 물어본 품목. `not_yet_items` 와 섞지 않는다."""
        return self._of("UNREADABLE")


def fold_readiness(gates: tuple[ItemForecastGate, ...]) -> DayReadiness:
    """품목별 답을 날 하나로. 접는 규칙이 여기 한 곳에만 있다."""
    if any(gate.readiness == "UNREADABLE" for gate in gates):
        return "UNREADABLE"
    ready = sum(1 for gate in gates if gate.readiness == "READY")
    if ready == len(gates):
        return "ALL_READY"
    if ready == 0:
        return "NONE_READY"
    return "SOME_READY"
