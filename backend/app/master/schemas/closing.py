"""마감 결과 모델.

★ 2026-09-30 재구성 BL-018: `master/closing.py` 에서 옮겼다 — `ClosingOut`.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.contracts.parts import ClosingPartOut


class ClosingOut(BaseModel):
    """마감 1회의 결과. **`ReceivableOut` 과 같은 다섯 갈래다.**

    ```text
    CLOSED       그날을 닫았다 (새로 적은 건수는 `created` 가 나른다)
    NOTHING_DUE  **그날 닫을 움직임이 없다** — 또는 미등록이다
    BLOCKED      **장부가 안 서서 못 닫는다**
    NOT_OPENED   **그날 장부가 안 열렸다** — 닫을 것이 있는지조차 묻지 않았다
    FAILED       닫아 보다 터졌다 — **아무것도 안 바뀌었다**
    ```

    🔴 **`NOTHING_DUE` 를 `BLOCKED` 로 접지 않는다** (`inbound.py` 의 그 규칙 그대로 ·
       물류 지적 2026-09-06 에서 배운 것이다).

      ```text
      NOTHING_DUE   실제로 그날 닫을 움직임이 없음
      BLOCKED       움직임은 있었는데 장부가 안 서서 못 닫음
      ```

      ⚠️ 접으면 **막힌 마감을 화면이 정상으로 오해한다.** 손익 곡선에 그 날이 빈
        칸으로 남는데, *"그날은 아무 일도 없었다"* 와 **똑같이** 보인다.

    🔴 **`NOT_OPENED` 를 `BLOCKED` 로 접지 않는다.**

      ```text
      BLOCKED      **장부가 안 섰다** — 입고·채권·수금 중 무엇이 막았는지가 사유에 있다
      NOT_OPENED   **아직 아무것도 안 봤다** — 하루가 안 열려 물어보지도 못했다
      ```

      ⚠️ 접으면 *"어제 입고가 막혔다"* 와 *"어제 개장을 안 돌렸다"* 가 같은 문장으로
        나간다. **고칠 곳이 완전히 다른데** 화면은 같아 보인다.

      ★ **다음에 할 일도 다르다** — 그래서 `next_action` 을 같이 싣는다.

    ★ **파트가 `BLOCKED` 면 전체도 `BLOCKED` 다.** 한 파트가 닫았더라도 그렇다 —
      *"닫을 게 있었는데 못 닫았다"* 가 *"닫았다"* 보다 먼저 알려야 하는 사실이다.
    """

    as_of: date
    status: Literal["CLOSED", "NOTHING_DUE", "BLOCKED", "NOT_OPENED", "FAILED"]
    reason: str = ""
    parts: list[ClosingPartOut] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    #: 🔴 **키가 항상 있고 막히지 않았으면 `None` 이다.** `ReceivableOut.next_action`
    #: 과 같은 모양이다. `NOT_OPENED` 일 때 개장 Gate 가 준 값을 **해석하지 않고
    #: 그대로** 옮긴다.
    next_action: str | None = None
