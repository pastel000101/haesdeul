"""입고 실행 결과 모델."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.contracts.parts import InboundPartOut


class InboundOut(BaseModel):
    """입고 실행 1회의 결과. 다섯 갈래다.

    ```text
    RECEIVED     한 파트라도 실제로 받았다
    NOTHING_DUE  받을 대상이 없었다 — 오늘 도착 예정이 없었거나 미등록이다
    BLOCKED      받을 대상은 있는데 처리할 수 없다 — purchase 참조 누락 · 깨진 상태
    NOT_OPENED   그날 장부가 안 열렸다 — 받을 것이 있는지조차 묻지 않았다
    FAILED       받으려다 실패했다 — 아무것도 바뀌지 않았다
    ```

    `NOT_OPENED` 와 `BLOCKED` 는 다른 값이다. `BLOCKED` 는 받을 대상이 있는데 그 건을
    처리할 수 없는 것이고 해당 `inbound_id` 가 나온다. `NOT_OPENED` 는 장부가 없어 아무것도
    확인하지 못한 것이다. 다음에 할 일도 다르다 — `BLOCKED` 는 그 건을 봐야 하고,
    `NOT_OPENED` 는 `open_day` 를 부르면 된다. 그래서 `next_action` 을 함께 싣는다.

    `BLOCKED` 와 `NOTHING_DUE` 도 다른 값이다. `NOTHING_DUE` 는 받을 대상이 실제로 없는
    것이고, `BLOCKED` 는 대상은 있지만 처리할 수 없는 것이다. 둘을 한 값으로 보면
    `purchase_id` 누락이나 깨진 참조로 막힌 날이 "올 것이 없던 날" 로 보인다.

    파트가 하나라도 `BLOCKED` 면 다른 파트가 받았더라도 전체가 `BLOCKED` 다. 받을 것이
    있었는데 못 받았다는 사실을 먼저 알린다.
    """

    as_of: date
    status: Literal["RECEIVED", "NOTHING_DUE", "BLOCKED", "NOT_OPENED", "FAILED"]
    reason: str = ""
    parts: list[InboundPartOut] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    #: 키가 항상 있고 막히지 않았으면 `None` 이다. `DayGate.next_action` 과 같은
    #: 모양이다 — 칸을 없애면 화면이 `'next_action' in resp` 를 먼저 물어야 한다.
    #: `NOT_OPENED` 일 때 개장 Gate 가 준 값을 해석하지 않고 그대로 나른다.
    next_action: str | None = None
