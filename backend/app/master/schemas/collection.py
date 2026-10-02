"""수금 실행 결과 모델."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.contracts.parts import CollectionPartOut


class CollectionOut(BaseModel):
    """수금 실행 1회의 결과. `InboundOut` 과 같은 다섯 갈래다.

    ```text
    COLLECTED    한 파트라도 실제로 수금했다
    NOTHING_DUE  수금할 대상이 없었다 — 오늘 수금 사건이 없었거나 미등록이다
    BLOCKED      수금할 대상은 있는데 반영할 수 없다 — 축 불일치 · 깨진 원장
    NOT_OPENED   그날 장부가 안 열렸다 — 수금할 것이 있는지조차 묻지 않았다
    FAILED       반영하려다 실패했다 — 아무것도 바뀌지 않았다
    ```

    `NOT_OPENED` 와 `BLOCKED` 는 다른 값이다. `BLOCKED` 는 수금할 대상이 있는데 그 건을
    반영할 수 없는 것이고 해당 `receivable_id` 가 나온다. `NOT_OPENED` 는 장부가 없어 아무것도
    확인하지 못한 것이다. 다음에 할 일도 다르다 — `BLOCKED` 는 그 채권을 봐야 하고,
    `NOT_OPENED` 는 `open_day` 를 부르면 된다. 그래서 `next_action` 을 함께 싣는다.

    `BLOCKED` 와 `NOTHING_DUE` 도 다른 값이다. `NOTHING_DUE` 는 수금할 대상이 실제로 없는
    것이고, `BLOCKED` 는 대상은 있지만 반영할 수 없는 것이다. 둘을 한 값으로 보면 축
    불일치나 깨진 원장으로 막힌 날이 "들어올 것이 없던 날" 로 보이고, 들어왔어야 할 현금이
    장부에 없는 채로 매입 판단이 돈다.

    파트가 하나라도 `BLOCKED` 면 다른 파트가 수금했더라도 전체가 `BLOCKED` 다. 들어올 것이
    있었는데 못 받았다는 사실을 먼저 알린다.
    """

    as_of: date
    status: Literal["COLLECTED", "NOTHING_DUE", "BLOCKED", "NOT_OPENED", "FAILED"]
    reason: str = ""
    parts: list[CollectionPartOut] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    #: 키가 항상 있고 막히지 않았으면 `None` 이다. `DayGate.next_action` 과 같은
    #: 모양이다 — 칸을 없애면 화면이 `'next_action' in resp` 를 먼저 물어야 한다.
    #: `NOT_OPENED` 일 때 개장 Gate 가 준 값을 해석하지 않고 그대로 나른다.
    next_action: str | None = None
