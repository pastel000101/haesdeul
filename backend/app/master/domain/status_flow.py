"""상태 조회 Flow 의 결과 모델 — 실행은 `service/status_flow.py`.

★ 2026-09-30 재구성 BL-018: `master/status_flow.py` 에서 옮겼다 — `StatusOutcome`. 결과 어휘
  `StatusCode` 는 `schemas/status_flow.py`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.contracts.envelope import AgentName
from app.master.domain.plan import ExecutionPlan
from app.master.schemas.status_flow import StatusCode


@dataclass(frozen=True)
class StatusOutcome:
    """조회 한 번의 결과. **무엇을 못 봤는지도 담는다.**"""

    status_code: StatusCode
    reason: str
    plan: ExecutionPlan

    answers: Mapping[AgentName, Mapping[str, Any]] = field(default_factory=dict)
    #: 물었는데 못 답한 부서. 조용히 빼지 않는다 — 빈 답과 못 받은 답은 다르다.
    unavailable: tuple[AgentName, ...] = ()
    #: 각 부서가 "무엇이 없어서" 못 답했는지 (`RUNTIME_NOT_READY`).
    missing_data: Mapping[AgentName, tuple[str, ...]] = field(default_factory=dict)
    #: 호출이 **터진** 부서와 사유 (`ERROR`).
    #:
    #: ★ `missing_data` 와 나눈다. 둘 다 밴드에 기여하지 않아 fail-safe 는 같지만
    #:   **재시도 가치가 다르다** — 실행 실패(예외·타임아웃)는 다시 불러 볼 값어치가
    #:   있고, 입력이 없어서 못 낸 답은 다시 불러도 같다 (`ports.error_reply` 주석).
    #:   한 칸에 담으면 어댑터가 터진 날과 값이 없던 날이 이력에서 같아 보인다.
    errors: Mapping[AgentName, str] = field(default_factory=dict)

    @property
    def runtime_status(self) -> str:
        """적재용. **아무도 못 답한 경우만 미가동이다.**

        일부라도 답했으면 돌긴 돈 날이다 — 매입 Flow 에서 `E4` 만
        `RUNTIME_NOT_READY` 인 것과 같은 구분이다.
        """
        return "RUNTIME_NOT_READY" if self.status_code == "S3_UNAVAILABLE" else "READY"
