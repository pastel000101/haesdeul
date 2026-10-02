"""승인 전이 결과 모델."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class TransitionOut(BaseModel):
    """승인 1건의 상태전이 결과. 네 값을 섞지 않는다.

    ```text
    APPLIED                    매입 원장과 재무 · 물류 장부가 한 트랜잭션으로 바뀌었다
    NOT_APPLIED                전이를 불렀지만 쓸 것이 없었다 — 전이 파트 미등록이거나
                               원장에 쓸 수 없는 약정이다 (갈래는 `block_kind`)
    FAILED                     바꾸려다 실패했다 — 아무것도 바뀌지 않았다
    AWAITING_PURCHASE_RECORD   전이를 부르지 않았다 — 실매입 기록을 기다린다
    ```

    `NOT_APPLIED` 를 `FAILED` 로 보면 쓸 수 없던 것이 장애로 읽히고, 반대로 보면 실패한
    전이가 "안 돌았다" 로 묻힌다.

    사람 승인은 전이를 부르지 않고 `AWAITING_PURCHASE_RECORD` 를 싣는다. 실매입을 기록하면
    그 기록값으로 전이가 선다. 자동 승인(`AUTO-BACKFILL`)은 승인 즉시 계획값으로 전이한다.
    `AWAITING_PURCHASE_RECORD` 는 `NOT_APPLIED` 와 다르다 — 저쪽은 불렀는데 쓸 것이
    없었다는 뜻이고 이쪽은 아직 부를 차례가 아니라는 뜻이라, 화면이 할 일(기록 폼을 연다)이
    다르다.
    """

    status: Literal["APPLIED", "NOT_APPLIED", "FAILED", "AWAITING_PURCHASE_RECORD"]
    reason: str = ""
    #: 실제로 write 를 낸 파트. `APPLIED` 가 아니면 비어 있다.
    parts: list[str] = Field(default_factory=list)
    #: 등록소에 어댑터가 없는 파트(미등록).
    missing: list[str] = Field(default_factory=list)
    #: 이미 열려 있어 같이 실어 준 다음 날들. 비어 있는 것이 정상이다 — 정방향이면
    #: 내일이 아직 없다. 값이 있으면 "앞질러 열린 장부를 따라잡았다" 는 사실이고, 화면에
    #: 나가 왜 하루가 여러 번 바뀌었는지를 설명한다.
    #:
    #: 열린 날이 아니라 실제로 쓴 날이다 (`#381`). 열려 있었지만 도착일이 이미 지나 실을
    #: 회차가 없던 날은 여기 안 들어간다. 화면이 "따라잡았다" 고 말하는 날과 행이 실제로
    #: 선 날이 갈리면, 그 문장은 근거가 아니라 장식이다.
    carried_forward: list[date] = Field(default_factory=list)
    #: 빈 목록이 두 가지 뜻이면 안 된다 (물류 지적 2026-09-07).
    #:
    #: ```text
    #: OK          앞질러 열린 날이 없었다 — 정방향이다
    #: UNREADABLE  개장 정본을 못 읽었다 — 있었는지조차 모른다
    #: ```
    #:
    #: 둘 다 `carried_forward=[]` 로 나가면, 낡은 미래 행이 남아 있는데도 화면은 "따라잡을
    #: 것이 없었다" 로 읽는다. 없는 것과 못 읽은 것은 다르다 — `day_gate` 가 근사를
    #: 근사라고 적는 것과 같은 자리다.
    #:
    #: `UNREADABLE` 이어도 승인은 선다. 못 읽는 것이 승인을 멈추면 안 된다.
    carried_forward_status: Literal["OK", "UNREADABLE"] = "OK"

    #: 원장에 한 행도 안 남은 이유의 갈래. 막힌 게 아니면 빈 값.
    #:
    #: `reason` 만으로는 셀 수가 없다. 문장에 등급 이름과 회차 번호가 박혀 있어 약정마다
    #:   다른 키가 된다 (확인 걷기 실측: 6건 726kg 255,287원이 `NOT_APPLIED` 한 값에 묻혀
    #:   요약에 안 보였다).
    #:
    #: 이름의 주인은 `domain/ledger.py` 의 `LEDGER_BLOCK_KINDS` 다 — 여기서 안 짓는다.
    #:
    #: 주의: 이 칸은 흐름을 안 가른다. 세는 쪽만 읽는다 — 자동 승인이 고르는 안도
    #:   재시도가 도는 횟수도 이 칸으로 바뀌지 않는다.
    block_kind: str = ""
