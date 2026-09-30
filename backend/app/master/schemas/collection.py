"""수금 실행 결과 모델.

★ 2026-09-30 재구성 BL-018: `master/collection.py` 에서 옮겼다 — `CollectionOut`.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.contracts.parts import CollectionPartOut


class CollectionOut(BaseModel):
    """수금 실행 1회의 결과. **`InboundOut` 과 같은 다섯 갈래다.**

    ```text
    COLLECTED    한 파트라도 실제로 수금했다
    NOTHING_DUE  **수금할 대상이 없었다** — 오늘 수금 사건이 없었거나 미등록이다
    BLOCKED      **수금할 대상은 있는데 반영할 수 없다** — 축 불일치 · 깨진 원장
    NOT_OPENED   **그날 장부가 안 열렸다** — 수금할 것이 있는지조차 묻지 않았다
    FAILED       반영하려다 실패했다 — **아무것도 안 바뀌었다**
    ```

    🔴 **`NOT_OPENED` 를 `BLOCKED` 로 접지 않는다.**

      ```text
      BLOCKED      수금할 대상이 있는데 **그 건이** 반영 불가 — receivable_id 가 나온다
      NOT_OPENED   **아직 아무것도 안 봤다** — 장부가 없어 물어보지도 못했다
      ```

      ⚠️ 접으면 *"채권에 문제가 있다"* 와 *"어제 개장을 안 돌렸다"* 가 같은 문장으로
        나간다. **고칠 곳이 완전히 다른데** 화면은 같아 보인다.

      ★ **다음에 할 일도 다르다.** `BLOCKED` 는 그 채권을 봐야 하고, `NOT_OPENED` 는
        `open_day` 를 부르면 된다 — 그래서 `next_action` 을 같이 싣는다.

    🔴 **`BLOCKED` 를 `NOTHING_DUE` 로 접지 않는다** (`inbound.py` 의 그 규칙 그대로 ·
       물류 지적 2026-09-06 에서 배운 것이다).

      ```text
      NOTHING_DUE   실제로 수금할 대상이 없음
      BLOCKED       수금할 대상은 존재하지만 반영할 수 없음
      ```

      ⚠️ 접으면 **막힌 수금을 뒤의 orchestration 이 정상으로 오해한다.** 축 불일치나
        깨진 원장으로 막힌 날이 *"오늘은 들어올 게 없었다"* 로 보이고, **들어왔어야 할
        현금이 장부에 없는 채로** 매입 판단이 돈다.

      ★ **파트가 `BLOCKED` 면 전체도 `BLOCKED` 다.** 한 파트라도 수금했더라도 그렇다 —
        *"들어올 게 있었는데 못 받았다"* 가 *"받았다"* 보다 먼저 알려야 하는 사실이다
        (`day_open` 이 `REJECTED_GAP` 을 먼저 보는 것과 같은 판단).
    """

    as_of: date
    status: Literal["COLLECTED", "NOTHING_DUE", "BLOCKED", "NOT_OPENED", "FAILED"]
    reason: str = ""
    parts: list[CollectionPartOut] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    #: 🔴 **키가 항상 있고 막히지 않았으면 `None` 이다.** `DayGate.next_action` 과 같은
    #: 모양이다 — 칸을 없애면 화면이 `'next_action' in resp` 를 먼저 물어야 한다.
    #: `NOT_OPENED` 일 때 개장 Gate 가 준 값을 **해석하지 않고 그대로** 옮긴다.
    next_action: str | None = None
