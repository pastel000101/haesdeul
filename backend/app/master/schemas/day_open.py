"""하루 넘김(개장) 결과 모델과 개장 정본 행.

★ 2026-09-30 재구성 BL-018: `master/day_open.py` 에서 옮겼다 — `DayOpenPartOut`, `DayOpenOut`.
★ 2026-09-30 재구성 BL-018: `master/day_opening_repository.py` 에서 옮겼다 — `DayOpeningRecord`.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.master.schemas.collection_seed import SeedStatus


class DayOpenPartOut(BaseModel):
    """한 파트의 하루 넘김 결과.

    ★ **파트마다 따로 낸다.** 재무와 물류가 서로 다른 날까지 열려 있을 수 있고, 한
      파트가 막혔다고 다른 파트를 되돌리지 않는다 (C.1).
    """

    part: str
    #: 🔴 **계약 어휘 셋이다** (`260904_마스터_전달_재무물류_open_day_파트계약` §어휘).
    #:
    #:   ```text
    #:   PART_OPENED           이번 호출로 내 몫을 열었다
    #:   PART_ALREADY_OPENED   이미 열려 있었다 (멱등 no-op)
    #:   PART_FAILED           못 열었다
    #:   ```
    #:
    #: ⚠️ 전에는 `OPENED | BLOCKED` 였고 *"이미 열려 있었다"* 를 `opened` 가 빈
    #:    목록인 것으로 표현했다. **계약을 내고 그보다 작게 만든 자리**였다
    #:    (재무가 계약 어휘로 회신해 와서 드러났다 · 2026-09-06).
    status: Literal["PART_OPENED", "PART_ALREADY_OPENED", "PART_FAILED"]
    reason: str = ""
    #: 이번에 만든 날. 오래된 날부터이며 **하루도 건너뛰지 않는다.**
    opened: list[date] = Field(default_factory=list)
    #: 🔴 상한을 넘겨 막혔으면 **밀린 날 수.** 마스터가 전체를 `REJECTED_GAP` 으로
    #:   올리는 근거다.
    #:
    #: ★ **사유 문자열을 읽지 않으려고 칸으로 둔다.** 마스터가 파트의 말을 해석하기
    #:   시작하면 `§3.2.5` 가 무너진다 — 그건 `next_action` 을 횟수로 가르는 것과
    #:   같은 판단이다.
    gap_days: int | None = None


class DayOpenOut(BaseModel):
    """하루 넘김 1회의 결과.

    🔴 **세 값을 섞지 않는다** (`TransitionOut` 과 같은 결).

      ```text
      OPENED         한 파트라도 이번에 열었다
      ALREADY_OPENED 전부 이미 열려 있었다 — 할 일이 없었다
      NOT_OPENED     한 파트라도 실패했다 · 미등록이다 · 커밋이 터졌다
      REJECTED_GAP   상한(31일)을 넘겨 거절했다 — 관리자 강제 개장이 필요하다
      ```

    🔴 **`ALREADY_OPENED` 를 `NOT_OPENED` 로 접지 않는다.** 앞은 *"할 일이 없었다"* 이고
       뒤는 *"못 했다"* 다. 접으면 **매일 도는 정상 상태가 실패로 보인다.**

    ⚠️ 전에는 `OPENED / NOT_OPENED / FAILED` 셋이었다 — 계약이 넷인데 구현이 셋이었고,
       `ALREADY_OPENED` 와 `REJECTED_GAP` 이 `NOT_OPENED` 안에 뭉쳐 있었다.
    """

    as_of: date
    status: Literal["OPENED", "ALREADY_OPENED", "NOT_OPENED", "REJECTED_GAP"]
    reason: str = ""
    #: 파트별 결과. `FAILED` 면 비어 있다 — 전부 되돌렸기 때문이다.
    parts: list[DayOpenPartOut] = Field(default_factory=list)
    #: 아직 구현이 없는 파트.
    missing: list[str] = Field(default_factory=list)
    #: 🔴 **수금 사건 생성 결과. 다섯 값을 섞지 않는다** (재무 조건 `⑥`).
    #:
    #: ```text
    #: SEEDED         n 건 만들었다
    #: NOTHING_DUE    **확인했고** 만들 것이 없었다 (0 건)
    #: UNREADABLE     **못 했다** — 조회나 쓰기가 실패했다
    #: BLOCKED        **막았다** — 실행 축이 안 맞아 fail-closed 했다
    #: NOT_ATTEMPTED  시도할 **이유가 없었다** — 하루가 안 열렸다
    #: ```
    #:
    #: ★ 낱말의 주인은 `collection_seed.SeedStatus` 다. 여기는 그 값을 실어 나른다.
    #:
    #: ⚠️ `UNREADABLE` 을 `NOTHING_DUE` 로 접으면 표가 안 서 있는 날이 *"확인했고
    #: 없었다"* 로 조용히 지나간다 — `carried_forward_status` 와 같은 규율이다.
    #:
    #: ★ **개장을 실패시키지 않는다.** 사건 생성이 터져도 하루는 열려야 하고, 못 했다는
    #: 사실만 응답에 실린다.
    collection_seed_status: SeedStatus = "NOT_ATTEMPTED"
    #: 이번 개장으로 만든 수금 사건 수.
    collection_seeded: int = 0
    #: 이미 있어 건너뛴 수금 사건 수. **멱등이 실제로 걸렸다는 근거다.**
    collection_seed_skipped: int = 0
    #: 못 했거나 시도하지 않은 이유. 성공이면 빈 문자열이다.
    collection_seed_reason: str = ""


class DayOpeningRecord:
    """개장 정본 한 행. **읽기 전용 값이다.**"""

    __slots__ = ("as_of", "attempt_count", "failure_count", "reason", "result", "sim_run_id")

    def __init__(
        self,
        *,
        as_of: date,
        sim_run_id: str,
        result: str,
        attempt_count: int,
        failure_count: int,
        reason: str | None,
    ) -> None:
        self.as_of = as_of
        self.sim_run_id = sim_run_id
        self.result = result
        self.attempt_count = attempt_count
        self.failure_count = failure_count
        self.reason = reason
