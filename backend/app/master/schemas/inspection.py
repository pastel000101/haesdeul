"""물류 점검 단계 결과 모델과 점검 시점 어휘."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Literal, get_args

from app.logistics.schemas.monitoring import DetectOut, DetectPhase

#: 입고 직후. 그날 점유가 뛴 자리라 용량 압박이 여기서 잡힌다. 신선도도 함께 보는
#: 이유는 하루가 지났기 때문이다 — 그날 매입 판단이 그 사실을 볼 수 있어야 한다.
AFTER_INBOUND: DetectPhase = "AFTER_INBOUND"
#: 출고 직후. 그날 조건을 없앤 사건이 다 끝난 자리라 해소(RESOLVED)가 여기서 난다.
AFTER_OUTBOUND: DetectPhase = "AFTER_OUTBOUND"

#: 점검 한 칸의 결과 어휘. 주인은 이 한 줄이다 — `InspectionOut.status` 도 이것을
#: 가리키고, 걷기 요약이 0건을 채울 때도 이것을 읽는다 (`envelope.LLM_STATUSES` 와 같은 모양).
InspectionStatus = Literal["RAN", "NOTHING_DUE", "FAILED"]
INSPECTION_STATUSES: frozenset[str] = frozenset(get_args(InspectionStatus))


@dataclass(frozen=True)
class InspectionOut:
    """물류 점검 한 칸의 결과. 예외 대신 이것을 돌려준다.

    ```text
    RAN           문제를 열었거나 갱신했거나 닫았다
    NOTHING_DUE   확인했고 손댈 것이 없었다 — 정상이다
    FAILED        보려다 터졌다 — 아무것도 안 바뀌었다
    ```

    어휘를 새로 만들지 않는다. 셋 다 `MaintenanceOut.status` 그대로이고, 칸을 안 탄
    날의 `NOT_ATTEMPTED` 도 `DayRunOutcome` 이 쓰는 말이다.

    `NOTHING_DUE` 와 `FAILED` 를 접지 않는다. "문제가 없었다" 와 "문제가 있었는지조차
    못 물어봤다" 는 다르고, 접으면 표가 없는 DB 에서 이 칸이 조용해진다.
    """

    as_of: date
    phase: str
    status: InspectionStatus
    reason: str = ""
    #: 물류가 낸 값 그대로. 접지 않는다 — 무엇을 열고 갱신하고 닫았는지의
    #: 주인은 `DetectOut` 하나다.
    result: DetectOut | None = None

    @property
    def counts(self) -> Mapping[str, int]:
        """연 것 · 갱신 · 닫은 것. 여기서 다시 세지 않는다."""
        return {} if self.result is None else self.result.counts
