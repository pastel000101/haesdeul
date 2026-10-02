"""재무 쓰기 service 가 요청을 받지 않았다 — 화면 라우터와 마스터 ask 가 같이 받는 업무 예외.

재무 쓰기 6종의 service 가 이 예외 하나로 무엇이 막혔는지만 말하고, 상태 코드와 문장으로 옮기는
것은 부르는 쪽이 한다 — 라우터는 404 · 409 · 422, 마스터 ask 는 `LookupError` · `DecisionRejected`
(마스터 라우터가 같은 404 · 409 · 422 와 같은 문장으로 접는다).

```text
reason       뜻                                    화면     ask
NOT_FOUND    대상(거래처 · 채권 · 비용)이 없다      404      LookupError
CONFLICT     지금 원장 상태로는 받을 수 없다         409      DecisionRejected(conflict=True)
INVALID      요청 자체가 모자라다                    422      DecisionRejected
```
"""

from typing import Literal

RejectionReason = Literal["NOT_FOUND", "CONFLICT", "INVALID"]


class FinanceWriteRejected(Exception):
    """재무 쓰기를 받지 않았다. `message` 는 사용자에게 그대로 보이는 문장이다."""

    def __init__(self, reason: RejectionReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
