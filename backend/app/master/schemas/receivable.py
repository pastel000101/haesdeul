"""채권 발행 결과 모델.

★ 2026-09-30 재구성 BL-018: `master/receivable.py` 에서 옮겼다 — `ReceivableOut`.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.contracts.parts import ReceivablePartOut


class ReceivableOut(BaseModel):
    """채권 발행 1회의 결과. **`CollectionOut` 과 같은 다섯 갈래다.**

    ```text
    ISSUED       그날 대상이 있었고 채권이 서 있다 (새로 만든 건수는 `created` 가 나른다)
    NOTHING_DUE  **그날 sale_date 인 확정 판매가 없다** — 또는 미등록이다
    BLOCKED      **대상은 있는데 권위 있는 입력이 없어 못 만든다** — 축 불일치 · 기일 없음
    NOT_OPENED   **그날 장부가 안 열렸다** — 세울 것이 있는지조차 묻지 않았다
    FAILED       세워 보다 터졌다 — **아무것도 안 바뀌었다**
    ```

    🔴 **`NOTHING_DUE` 를 `BLOCKED` 로 접지 않는다** (`inbound.py` 의 그 규칙 그대로 ·
       물류 지적 2026-09-06 에서 배운 것이다).

      ```text
      NOTHING_DUE   실제로 그날 확정된 판매가 없음
      BLOCKED       확정 판매는 존재하지만 채권을 못 세움
      ```

      ⚠️ 접으면 **막힌 발행을 뒤의 orchestration 이 정상으로 오해한다.** 축 불일치나
        `collection_due_date` 결측으로 막힌 날이 *"오늘은 판 게 없었다"* 로 보이고,
        **서 있어야 할 채권이 장부에 없는 채로** 매입 판단이 돈다 —
        `receivables_krw` 는 매입 cap 이 보는 값이다.

    🔴 **`NOT_OPENED` 를 `BLOCKED` 로 접지 않는다.**

      ```text
      BLOCKED      세울 대상이 있는데 **그 건이** 발행 불가 — sale_id 가 나온다
      NOT_OPENED   **아직 아무것도 안 봤다** — 장부가 없어 물어보지도 못했다
      ```

      ⚠️ 접으면 *"판매 데이터에 문제가 있다"* 와 *"어제 개장을 안 돌렸다"* 가 같은
        문장으로 나간다. **고칠 곳이 완전히 다른데** 화면은 같아 보인다.

      ★ **다음에 할 일도 다르다.** `BLOCKED` 는 그 판매를 봐야 하고, `NOT_OPENED` 는
        `open_day` 를 부르면 된다 — 그래서 `next_action` 을 같이 싣는다.

    ★ **파트가 `BLOCKED` 면 전체도 `BLOCKED` 다.** 한 건이라도 섰더라도 그렇다 —
      *"세울 게 있었는데 못 세웠다"* 가 *"세웠다"* 보다 먼저 알려야 하는 사실이다.
    """

    as_of: date
    status: Literal["ISSUED", "NOTHING_DUE", "BLOCKED", "NOT_OPENED", "FAILED"]
    reason: str = ""
    parts: list[ReceivablePartOut] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    #: 🔴 **키가 항상 있고 막히지 않았으면 `None` 이다.** `CollectionOut.next_action`
    #: 과 같은 모양이다. `NOT_OPENED` 일 때 개장 Gate 가 준 값을 **해석하지 않고
    #: 그대로** 옮긴다.
    next_action: str | None = None
