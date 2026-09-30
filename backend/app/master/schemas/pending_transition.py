"""미적용 전이 재시도 모델 — 대상 승인, 건별 결과, 단계 결과.

★ 2026-09-30 재구성 BL-018: `master/pending_transition.py` 에서 옮겼다 — `RetryOutcome`,
  `RetryStatus`, `PendingApproval`, `RetriedTransition`, `RetryOut`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

RetryOutcome = Literal["APPLIED", "NOT_APPLIED", "FAILED", "NOT_BUILDABLE"]
"""미적용 승인 하나를 다시 세운 결과. 🔴 **넷을 접지 않는다.**

```text
APPLIED         원장에 닿았다 — 이 판이 노리는 값이다
NOT_APPLIED     아직 쓸 것이 없다 (어댑터 미등록 · 회차 금액 미기재 · 도착분 없음)
FAILED          쓰려다 터졌다 — 아무것도 안 바뀌었다
NOT_BUILDABLE   약정을 못 만들어 전이를 부를 수조차 없었다
```

★ **앞의 셋은 `TransitionOut.status` 그대로다** — 여기서 새 이름을 붙이지 않는다.

🔴 **`NOT_BUILDABLE` 을 `FAILED` 로 접지 않는다.** 앞은 *"승인 응답에서 안을 못
  찾았다 · 리드타임이 없다"* 처럼 **전이 앞에서 끝난 일**이고 뒤는 *"장부를 바꾸려다
  터졌다"* 다 — 고칠 곳이 서로 다르다.
"""

RetryStatus = Literal["RAN", "NOTHING_DUE", "FAILED"]
"""단계 하나가 어떻게 됐나. 🔴 **`RetryOutcome` 과 축이 다르다** — 저쪽은 승인 하나다.

```text
RAN           미적용을 찾아 다시 세웠다 — **전부 성공했다는 뜻이 아니다**
NOTHING_DUE   확인했고 미적용이 없었다 — 🟢 정상이다
FAILED        찾다가 터졌다 (조회가 안 됐다) — 미적용이 있었는지조차 모른다
```

★ **어휘를 새로 만들지 않았다.** 셋 다 하루 순서가 이미 쓰는 말이다
  (`InboundOut` · `CollectionOut` · `DayRunOutcome`).

🔴 **`NOTHING_DUE` 와 `FAILED` 를 접지 않는다.** *"미적용이 없다"* 와 *"미적용이
  있었는지 못 물어봤다"* 는 다르고, 접으면 조회가 죽은 날 이 단계가 조용해진다.
"""


@dataclass(frozen=True)
class PendingApproval:
    """승인은 났는데 **매입 원장에 안 닿은** 결정 하나."""

    request_id: str
    decision_seq: int
    #: 승인이 선 날 (실행 이력 행의 `as_of`).
    as_of: date
    #: 어느 실행의 장부인가. 🔴 **실행 이력 행이 정본이다** — 여기서 짓지 않는다.
    sim_run_id: str


@dataclass(frozen=True)
class RetriedTransition:
    """미적용 하나를 다시 세운 결과. **터진 것도 값으로 남는다.**"""

    request_id: str
    decision_seq: int
    as_of: date
    outcome: RetryOutcome
    #: 왜 그 결과가 됐나. `APPLIED` 에는 없다.
    reason: str = ""
    #: 🔴 **원장에 한 행도 안 남은 이유의 갈래** (2026-09-16). 막힌 게 아니면 빈 값.
    #:
    #: ★ **이름의 주인은 `ledger.LEDGER_BLOCK_KINDS` 다** — 여기서 안 짓는다.
    #:   `TransitionOut.block_kind` 를 **그대로 옮긴다**.
    #:
    #: ⚠️ **이 칸이 재시도를 멈추지 않는다.** 영영 안 될 갈래도 다음 날 또 세운다 —
    #:   여기서 거르면 걷기가 고르는 것이 바뀐다. 이 칸은 **세는 쪽만 읽는다.**
    block_kind: str = ""


@dataclass(frozen=True)
class RetryOut:
    """재시도 한 번의 결과. **예외 대신 이것을 돌려준다.**"""

    status: RetryStatus
    #: 단계가 왜 그렇게 됐나. `_stage` 가 note 로 싣는 자리다.
    reason: str = ""
    #: 다시 세워 본 승인마다 하나씩. **본 순서 그대로.**
    retried: tuple[RetriedTransition, ...] = field(default_factory=tuple)

    @property
    def outcomes(self) -> Mapping[str, int]:
        """결과 분포. **넷을 그대로 센다** — 새 이름을 안 붙인다.

        ★ `BackfillOut.outcomes` 와 같은 모양이다. 요약이 이 값을 그대로 싣는다.
        """
        counted: dict[str, int] = {}
        for one in self.retried:
            counted[one.outcome] = counted.get(one.outcome, 0) + 1
        return counted
