"""자동 정비 단계 결과 모델.

★ 2026-09-30 재구성 BL-018: `master/maintenance.py` 에서 옮겼다 — `MaintenanceOut`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.logistics.schemas.maintenance import AutoMaintenanceResult


@dataclass(frozen=True)
class MaintenanceOut:
    """유지보수 단계 1회의 결과. **예외 대신 이것을 돌려준다.**

    ```text
    RAN           손댔거나 일부러 건너뛴 Lot 이 있었다 — **전부 성공했다는 뜻이 아니다**
    NOTHING_DUE   확인했고 할 것이 없었다 — 🟢 정상이다
    FAILED        하려다 터졌다 — 아무것도 안 바뀌었다
    ```

    ★ **어휘를 새로 만들지 않았다.** 셋 다 하루 순서가 이미 쓰는 말이다
      (`RetryStatus` · `InboundOut` · `OutboundOut`), 단계를 안 탄 날의
      `NOT_ATTEMPTED` 도 `DayRunOutcome` 이 이미 쓴다.

    🔴 **`NOTHING_DUE` 와 `FAILED` 를 접지 않는다.** *"버릴 것이 없었다"* 와
       *"버릴 것이 있었는지조차 못 물어봤다"* 는 다르고, 접으면 조회가 죽은 날
       이 단계가 조용해진다.
    """

    as_of: date
    status: Literal["RAN", "NOTHING_DUE", "FAILED"]
    reason: str = ""
    #: 물류가 낸 값 **그대로**. 🔴 **접지 않는다** — 몇 Lot 을 봤고 무엇을 버렸고
    #: 무엇을 사람에게 남겼는지의 주인은 `AutoMaintenanceResult` 하나다.
    result: AutoMaintenanceResult | None = None

    @property
    def outcomes(self) -> Mapping[str, int]:
        """Lot 별 결과 분포. **`MaintenanceOutcome` 넷을 그대로 센다.**

        ★ `RetryOut.outcomes` · `BackfillOut.outcomes` 와 같은 모양이다 — 요약이
          이 값을 그대로 싣는다.

        🔴 **여기서 새 이름을 안 붙인다.** 넷의 주인은 `auto_maintenance.py` 다.
        """
        counted: dict[str, int] = {}
        if self.result is None:
            return counted
        for one in self.result.lots:
            counted[one.outcome] = counted.get(one.outcome, 0) + 1
        return counted
