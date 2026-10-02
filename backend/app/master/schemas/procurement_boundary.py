"""매입 경계 모델 — 판매가 넘겨받는 경계 값과 없는 이유 어휘."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

AbsentReason = Literal[
    "NOT_EXECUTION_DAY",
    "LEDGER_GAP",
    "NO_PROCUREMENT_RUN",
]
"""경계를 못 읽은 사유. 셋을 가르는 것이 이 모듈의 값이다.

```text
NOT_EXECUTION_DAY    그날은 실행일이 아니라 매입 판단이 안 돈다 (토·일·공휴일)
LEDGER_GAP           장부 관문이 막아서 판단을 안 돌렸다
NO_PROCUREMENT_RUN   실행일이고 관문도 안 막았는데 그날 행이 없다
```

매입 `basis` 의 `unknown` 을 푸는 자리다. 그 값은 "마스터가 안 실었다" 와 "실렸는데
계산이 안 됐다" 를 뭉갠다. 이 사유가 옆에 있으면 화면이 "토요일이라 못 물어봤다" 까지
말한다 — 셋을 한 낱말로 접으면 그 문장이 사라진다.

`NO_PROCUREMENT_RUN` 이 나머지 둘의 쓰레기통이 되면 안 된다. 그래서 판정 순서를
`read_procurement_boundary` 가 못 박고, 그 순서를 검사가 잠근다."""

ABSENT_REASONS: frozenset[str] = frozenset(get_args(AbsentReason))
"""닫힌 집합. 주인은 위 `Literal` 하나다 — `get_args` 로 읽어 두 벌로 만들지 않는다
(봉투가 `TRIGGERS` 를 만든 것과 같은 자리)."""


@dataclass(frozen=True)
class ProcurementBoundary:
    """그날 매입 판단이 받아 둔 경계 — 재료 넷과 그 출처.

    `present` 와 값 넷이 어긋나면 성립하지 않는다. 못 읽었다면서 값이 실려 있으면 받는
    쪽이 그 값을 쓴다. 아래 `__post_init__` 이 그것을 봉투처럼 만들 수 없게 막는다 —
    받아 보고 판정할 것이 아니라 애초에 나가면 안 되는 모양이다
    (`app/contracts/envelope.py` 의 두 층 중 앞쪽).
    """

    #: 경계를 읽었나.
    present: bool

    #: 못 읽었으면 왜. 읽었으면 `None`.
    absent_reason: AbsentReason | None = None

    #: 읽어 온 실행. `run_id` 와 `as_of` 를 담는다 (`domain/procurement_boundary.py`).
    source_ref: str | None = None

    warehouse_free_kg: float | None = None
    rental_cap_kg: float | None = None
    finance_cap_amount_krw: int | None = None
    inbound_lead_days: int | None = None

    def __post_init__(self) -> None:
        if self.present:
            if self.absent_reason is not None:
                raise ValueError("경계를 읽었는데 못 읽은 사유가 붙었다 — 둘 중 하나가 거짓이다.")
            if not self.source_ref:
                raise ValueError(
                    "경계를 읽었는데 출처가 없다 — 그 경계가 어느 실행의 언제 것인지는"
                    " 숨기지 않는다 (§3.2)."
                )
            return
        if self.absent_reason not in ABSENT_REASONS:
            raise ValueError(
                f"absent_reason={self.absent_reason!r} 는 사유 어휘가 아니다."
                f" 허용: {sorted(ABSENT_REASONS)}"
            )
        # 못 읽은 날에 값이 하나라도 실리면 그 값이 쓰인다. `0` 으로 채우지 않는 규율이
        # 여기서 한 번 더 잠긴다.
        실린값 = {
            "warehouse_free_kg": self.warehouse_free_kg,
            "rental_cap_kg": self.rental_cap_kg,
            "finance_cap_amount_krw": self.finance_cap_amount_krw,
            "inbound_lead_days": self.inbound_lead_days,
        }
        남은것 = sorted(name for name, value in 실린값.items() if value is not None)
        if 남은것:
            raise ValueError(f"경계를 못 읽었는데 값이 실렸다: {', '.join(남은것)}")
