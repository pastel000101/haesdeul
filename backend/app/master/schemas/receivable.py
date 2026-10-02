"""채권 발행 결과 모델."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.contracts.parts import ReceivablePartOut


class ReceivableOut(BaseModel):
    """채권 발행 1회의 결과. `CollectionOut` 과 같은 다섯 갈래다.

    ```text
    ISSUED       그날 대상이 있었고 채권이 서 있다 (새로 만든 건수는 파트별 `created`)
    NOTHING_DUE  그날 sale_date 인 확정 판매가 없다 — 또는 미등록이다
    BLOCKED      대상은 있는데 권위 있는 입력이 없어 못 만든다 — 축 불일치 · 기일 없음
    NOT_OPENED   그날 장부가 안 열렸다 — 세울 것이 있는지조차 묻지 않았다
    FAILED       세우려다 실패했다 — 아무것도 바뀌지 않았다
    ```

    `NOTHING_DUE` 와 `BLOCKED` 는 다른 값이다. `NOTHING_DUE` 는 그날 확정된 판매가 실제로
    없는 것이고, `BLOCKED` 는 확정 판매는 있지만 채권을 못 세운 것이다. 둘을 한 값으로 보면
    축 불일치나 `collection_due_date` 결측으로 막힌 날이 "판 것이 없던 날" 로 보이고,
    서 있어야 할 채권이 장부에 없는 채로 매입 판단이 돈다. 채권 잔액은 매입 상한(cap)
    판단에 쓰이는 값이다.

    `NOT_OPENED` 와 `BLOCKED` 도 다른 값이다. `BLOCKED` 는 세울 대상이 있는데 그 건을 발행할
    수 없는 것이고 해당 `sale_id` 가 나온다. `NOT_OPENED` 는 장부가 없어 아무것도 확인하지
    못한 것이다. 다음에 할 일도 다르다 — `BLOCKED` 는 그 판매를 봐야 하고, `NOT_OPENED` 는
    `open_day` 를 부르면 된다. 그래서 `next_action` 을 함께 싣는다.

    파트가 하나라도 `BLOCKED` 면 한 건이라도 섰더라도 전체가 `BLOCKED` 다. 세울 것이
    있었는데 못 세웠다는 사실을 먼저 알린다.
    """

    as_of: date
    status: Literal["ISSUED", "NOTHING_DUE", "BLOCKED", "NOT_OPENED", "FAILED"]
    reason: str = ""
    parts: list[ReceivablePartOut] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    #: 키가 항상 있고 막히지 않았으면 `None` 이다. `CollectionOut.next_action` 과 같은
    #: 모양이다. `NOT_OPENED` 일 때 개장 Gate 가 준 값을 해석하지 않고 그대로 나른다.
    next_action: str | None = None
