"""
parts.py — 하루 단계 파트 결과 계약

마스터의 하루 단계(입고 · 마감 · 수금 · 채권 발행)는 등록소에 꽂힌 파트 구현을 부르고,
파트는 이 모양으로 결과를 돌려준다. 마스터가 그것을 모아 단계 결과(`*Out`)를 만든다.

  단계        등록소 Protocol (마스터)                             파트 결과 (여기)
  입고        `app.master.registry.inbound.InboundExecution`       `InboundPartOut`
  마감        `app.master.registry.closing.ClosingPort`            `ClosingPartOut`
  수금        `app.master.registry.collection.CollectionSource`    `CollectionPartOut`
  채권 발행   `app.master.registry.receivable.ReceivableSource`    `ReceivablePartOut`

결과 타입을 공용 계약에 두는 것은 부서 구현이 결과 타입을 만들려고 마스터 단계 모듈을
import 하는 역방향 의존을 막기 위해서다. Protocol · 등록 함수 · 단계 집계 모델(`*Out`)은
마스터의 조립 · 집계라 마스터(`app/master/registry/`)에 있다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

__all__ = [
    "ClosingPartOut",
    "CollectionPartOut",
    "InboundPartOut",
    "ReceivablePartOut",
]


class InboundPartOut(BaseModel):
    """한 파트의 입고 실행 결과.

    `NOTHING_DUE` 를 `RECEIVED` 로 합치지 않는다. "받을 것이 없었다" 와 "받았다" 는
    다른 사실이고, 합치면 도착 예정을 못 잡는 오류가 매일 성공으로 보인다.
    """

    part: str
    status: Literal["RECEIVED", "NOTHING_DUE", "BLOCKED"]
    reason: str = ""
    #: 이번 호출에서 입고 처리 파이프라인을 성공적으로 완료한 건.
    #:
    #: 신규 재고화만이 아니다 (물류 확정). 이미 재고화된 건의 멱등 검증과
    #: 남은 schedule 정리 성공도 포함한다.
    #:
    #: ```text
    #: 01-28   received=[A]   재고 +3,587kg   신규 재고화 완료
    #: 01-29   received=[A]   재고 불변       기존 재고화를 다시 확인만 한다
    #: ```
    #:
    #: 주의: `received` 포함 여부만으로 이번 호출에서 새 Lot·Move 가 생겼다고 읽지
    #: 않는다. 신규 재고화 여부의 권위 사실은 Receipt · Lot · Move 원장이 갖는다.
    #:
    #: 물류 `_receive_one` 은 "마지막 성공 단계 다음부터 이어 처리한다" 이고,
    #: `PUTAWAY_DONE`·`CLOSED` 여도 `materialize` 로 기존 Lot·Move 를 검증하고 남은
    #: 일정을 걷은 뒤 성공으로 끝낸다 — 그 날도 자기 몫을 다 한 것이다.
    #:
    #: 빈 목록이면 이 호출에서 완료한 건이 없다 — 이미 다 끝났거나 받을 것이
    #: 없었다. 어느 쪽인지는 `status` 가 말한다.
    received: list[str] = Field(default_factory=list)


class ClosingPartOut(BaseModel):
    """한 파트의 마감 결과.

    `NOTHING_DUE` 를 `CLOSED` 로 합치지 않는다. "그날 닫을 움직임이 없었다" 와
    "닫았다" 는 다른 사실이고, 합치면 하루가 닫히지 않는 오류가 매일 성공으로 보인다.
    """

    part: str
    status: Literal["CLOSED", "NOTHING_DUE", "BLOCKED"]
    reason: str = ""
    #: 그날에 대해 서 있는 마감 행. `daily_closings` 의 키다.
    #:
    #: 금액을 여기 싣지 않는다. 그날 얼마가 나가고 잔액이 얼마인지의 권위
    #: 사실은 `daily_closings` 한 곳이 갖는다 — 여기에 복사해 두면 같은 사실의
    #: 주인이 둘이 되고, 롤백된 날 이 목록만 살아남는다. 금액 칸을 하나라도 열어 두면
    #: 거기에 값이 채워지고, 그러면 마스터가 손익을 계산하기 시작한다.
    #:
    #: 이번 호출에서 새로 만든 것만이 아니다. 멱등이라 이미 있으면 안 만들고,
    #: 그래도 마감은 서 있다. 새로 적은 건수는 `created` 가 따로 나른다.
    closed: list[str] = Field(default_factory=list)
    #: 이번 호출에서 실제로 새로 적은 건수.
    #:
    #: `closed` 와 한 값으로 접으면 안 된다. 접으면 "이미 닫혀 있어서 안
    #: 적었다" 와 "닫을 것이 없었다" 가 같아 보이고, 멱등 재실행이 매일
    #: "아무것도 안 했다" 로 읽힌다. `CLOSED` 인데 `created == 0` 인 것이
    #: 정상 상태다.
    #:
    #: 어댑터가 낸 값을 그대로 적는다. 여기서 `len(closed)` 로 다시 세지
    #: 않는다 — 세는 순간 마스터가 계산을 시작하고, 두 번째 걸음이 매일 새 행을
    #: 적은 것처럼 보인다.
    created: int = 0


class CollectionPartOut(BaseModel):
    """한 파트의 수금 실행 결과.

    `NOTHING_DUE` 를 `COLLECTED` 로 합치지 않는다. "들어올 것이 없었다" 와
    "들어왔다" 는 다른 사실이고, 합치면 수금 사건을 못 잡는 오류가 매일 성공으로 보인다.
    """

    part: str
    status: Literal["COLLECTED", "NOTHING_DUE", "BLOCKED"]
    reason: str = ""
    #: 이번 호출에서 수금 사건을 반영한 채권. `receivable_id` 들이다.
    #:
    #: 금액을 여기 싣지 않는다. 얼마가 들어왔는지의 권위 사실은 재무 원장
    #: (`receivables` · `finance_states`)이 갖는다 — 여기에 복사해 두면 같은 사실의
    #: 주인이 둘이 되고, 롤백된 날 이 목록만 살아남는다.
    #:
    #: 빈 목록이면 이 호출에서 반영한 채권이 없다 — 이미 다 수금됐거나 들어올
    #: 것이 없었다. 어느 쪽인지는 `status` 가 말한다.
    collected: list[str] = Field(default_factory=list)


class ReceivablePartOut(BaseModel):
    """한 파트의 채권 발행 결과.

    `NOTHING_DUE` 를 `ISSUED` 로 합치지 않는다. "그날 확정 판매가 없었다" 와 "채권이
    섰다" 는 다른 사실이고, 합치면 판매가 확정됐는데 채권이 서지 않는 오류가 매일
    성공으로 보인다.
    """

    part: str
    status: Literal["ISSUED", "NOTHING_DUE", "BLOCKED"]
    reason: str = ""
    #: 그날 대상 판매에 대해 서 있는 채권. `receivable_id` 들이다.
    #:
    #: 금액을 여기 싣지 않는다. 얼마짜리 채권인지의 권위 사실은 재무 원장
    #: (`receivables` · `finance_states`)이 갖는다 — 여기에 복사해 두면 같은 사실의
    #: 주인이 둘이 되고, 롤백된 날 이 목록만 살아남는다.
    #:
    #: 이번 호출에서 새로 만든 것만이 아니다. 멱등이라 이미 있으면 안 만들고,
    #: 그래도 채권은 서 있다. 새로 만든 건수는 `created` 가 따로 나른다.
    issued: list[str] = Field(default_factory=list)
    #: 이번 호출에서 실제로 새로 만든 건수.
    #:
    #: `issued` 와 한 값으로 접으면 안 된다. 접으면 "이미 있어서 안 만들었다"
    #: 와 "대상이 없었다" 가 같아 보이고, 멱등 재실행이 매일 "아무것도 안 했다" 로
    #: 읽힌다. `ISSUED` 인데 `created == 0` 인 것이 정상 상태다.
    created: int = 0
