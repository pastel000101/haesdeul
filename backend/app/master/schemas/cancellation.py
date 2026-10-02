"""승인 취소 결과 모델."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CancellationOut(BaseModel):
    """취소 한 번의 결과. `TransitionOut` 과 같은 세 갈래다.

    ```text
    CANCELLED     다섯 자리가 다 물렸다
    NOT_APPLIED   아직 그 부서가 안 돈다 — 물릴 방법이 없었다
    FAILED        물리려다 실패했다 — 아무것도 안 바뀌었다
    ```

    `NOT_APPLIED` 를 `FAILED` 로 접으면 미구현이 장애로 읽히고, 반대로 접으면 실패한
    취소가 "안 돌았다" 로 조용히 묻힌다.
    """

    status: Literal["CANCELLED", "NOT_APPLIED", "FAILED"]
    reason: str = ""
    #: 실제로 write 를 낸 파트. `CANCELLED` 가 아니면 비어 있다.
    parts: list[str] = Field(default_factory=list)
    #: 등록소에 취소 구현체가 없는 파트(미등록).
    missing: list[str] = Field(default_factory=list)
    #: 물린 매입 원장 header 수. 이미 취소된 것을 다시 물면 0 이다 (멱등).
    cancelled_purchases: int = 0
