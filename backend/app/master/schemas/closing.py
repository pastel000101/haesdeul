"""마감 결과 모델."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.contracts.parts import ClosingPartOut


class ClosingOut(BaseModel):
    """마감 1회의 결과. `ReceivableOut` 과 같은 다섯 갈래다.

    ```text
    CLOSED       그날을 닫았다 (새로 적은 건수는 파트별 `created` 가 나른다)
    NOTHING_DUE  그날 닫을 움직임이 없다 — 또는 미등록이다
    BLOCKED      장부가 안 서서 못 닫는다
    NOT_OPENED   그날 장부가 안 열렸다 — 닫을 것이 있는지조차 묻지 않았다
    FAILED       닫으려다 실패했다 — 아무것도 바뀌지 않았다
    ```

    `NOTHING_DUE` 와 `BLOCKED` 는 다른 값이다. `NOTHING_DUE` 는 그날 닫을 움직임이 실제로
    없었다는 뜻이고, `BLOCKED` 는 움직임은 있었지만 장부가 안 서서 못 닫았다는 뜻이다.
    둘을 한 값으로 보면 막힌 마감이 손익 곡선에서 아무 일도 없던 날과 똑같이 보인다.

    `NOT_OPENED` 와 `BLOCKED` 도 다른 값이다. `BLOCKED` 는 장부가 안 선 것이고 입고 ·
    채권 · 수금 중 무엇이 막았는지가 사유에 있다. `NOT_OPENED` 는 하루가 안 열려 아무것도
    확인하지 못한 것이다. 고칠 곳과 다음에 할 일이 다르므로 `next_action` 을 함께 싣는다.

    파트가 하나라도 `BLOCKED` 면 다른 파트가 닫았더라도 전체가 `BLOCKED` 다. 닫을 것이
    있었는데 못 닫았다는 사실을 먼저 알린다.
    """

    as_of: date
    status: Literal["CLOSED", "NOTHING_DUE", "BLOCKED", "NOT_OPENED", "FAILED"]
    reason: str = ""
    parts: list[ClosingPartOut] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    #: 키가 항상 있고 막히지 않았으면 `None` 이다. `ReceivableOut.next_action` 과 같은
    #: 모양이다. `NOT_OPENED` 일 때 개장 Gate 가 준 값을 해석하지 않고 그대로 나른다.
    next_action: str | None = None
