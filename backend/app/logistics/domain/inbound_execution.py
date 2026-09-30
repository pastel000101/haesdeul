"""도착 처리의 막힘 사유 어휘 · 표기와 검수 사실 · 검수 원천(Protocol).

★ 2026-09-30 재구성 BL-015: `logistics/inbound_execution.py` 에서 옮겼다. 검수 원천의 구현은
  `domain/simulated_inspection.py`
  (전량 PASS — 주인이 정한 MVP 가정)이고, 마스터 등록소 조립이 이것을 꽂는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Protocol

from app.logistics.domain.arrival import ArrivalBlockReason, ArrivalUnresolvedReason, DueInbound
from app.logistics.schemas.inspections import InspectionOutcome
from app.logistics.schemas.purchase_detail import PurchaseDetail

#: 도착 처리를 막은 사유.
#:
#: ★ **앞 둘은 새 어휘가 아니다.** `arrival` 이 이미 쓰는 값을 그대로 실어 나른다 —
#:   같은 사실에 두 어휘를 두지 않는다 (`Literal` 중첩은 PEP 586 이 평탄화한다).
InboundBlockReason = Literal[
    ArrivalBlockReason,
    ArrivalUnresolvedReason,
    #: 그날 운송 중 목록 자체를 **확인한 적이 없다** (`in_transit_status = UNRESOLVED`).
    #: 🔴 *"오늘 받을 것이 없다"* 가 아니다 — 뭉치면 모르는 것을 아는 것처럼 다룬다.
    "IN_TRANSIT_UNRESOLVED",
    #: 매입 참조는 있는데 그 줄이 없다 (`PurchaseDetailMissing`).
    "PURCHASE_DETAIL_MISSING",
    #: 한 `purchase_id` 에 매입 줄이 둘 이상이다 (`PurchaseDetailAmbiguous`).
    "PURCHASE_DETAIL_AMBIGUOUS",
    #: 검수 provider 가 이 입고의 사실을 주지 못했다.
    #: 🔴 그럴 때 대신 만들지 않는다 — 그것이 이 파일이 막으려는 일이다.
    "INSPECTION_FACT_UNAVAILABLE",
]


@dataclass(frozen=True)
class InspectionFact:
    """한 입고의 검수 사실. **물류가 만들지 않고 받는다.**

    ★ 세 값이 `record_inspection` 의 세 인자와 **그대로 짝**이다. 여기서 가공하지
      않는다 — 가공하면 provider 가 준 사실과 DB 에 적힌 사실이 갈린다.

    :param inspected_at: 검수 시각. **tz 를 단 값이어야 한다** (`TIMESTAMPTZ`).
        🔴 물류가 시계를 읽어 채우지 않는다 — 같은 시뮬레이션을 다시 돌리면 같은 값이
        나와야 한다. 검증은 `inspections.record_inspection` 이 한다.
    :param inspector: 검수자. NOT NULL 이고 **저장소에 시스템 행위자 규약이 없다.**
    :param outcome: 판정과 네 수량. 항등식 검증은 `inspections.validate_outcome` 이 한다.
    """

    inspected_at: datetime
    inspector: str
    outcome: InspectionOutcome


class InspectionProvider(Protocol):
    """검수 사실의 **권위 출처**. 물류 밖에서 온다.

    🔴 **저장소에 구현이 없는 것이 지금의 정직한 상태다.** 자동 검수 규칙(합격률·
       등급별 판정·수량 배분)을 정한 문서도 코드도 씨앗 데이터도 없다. 여기에 기본
       구현을 놓으면 **아무도 정한 적 없는 비율이 곧 업무 사실이 되어** 원가·폐기·
       판매 판단으로 흘러간다.

    ★ **`None` 은 실패가 아니라 부재다.**

      ```text
      InspectionFact  이 입고의 검수 사실을 안다
      None            이 입고의 검수 사실을 **모른다** → BLOCKED
      ```

      ⚠️ **`None` 으로 오류를 숨기지 않는다.** provider 안에서 조회가 터졌다면 그것은
         예외이지 부재가 아니다 — 올리면 마스터가 롤백하고 `FAILED` 로 만든다.

    ★ **항상 `None` 을 내는 provider 는 정당한 배선이다.** 검수 원천이 아직 없는 날
      *"받을 것은 있는데 검수 사실이 없다"* 가 `BLOCKED` 로 매일 보이는 것이 맞다.
      그것을 기본값으로 코드에 숨기지 않고 배선 자리에서 고르게 두는 것이 요점이다.

    🔴 **잠금을 쥔 채 불린다 — 구현이 지켜야 할 제약이 여기서 나온다.**

    ```text
    도착 전역 advisory (20260905, 2)
    → 그날 fixture 행 FOR UPDATE
    → provide()          ★ 여기. 두 잠금이 이미 걸려 있다
    → materialize
    → 마스터가 commit
    ```

       ```text
       MVP provider 는 빠르고 결정론적인 **사실 조회**여야 한다
       외부 HTTP · LLM · 장시간 I/O 를 하지 않는다
       commit · rollback · 커넥션 수명을 소유하지 않는다
       ```

       ⚠️ 여기서 느리면 **도착 처리 전체가 그동안 직렬화된 채 멈춘다** — 그 트랜잭션이
          끝날 때까지 다른 도착 처리도, 승인 전이(`persist_inventory`)의 그날 행 갱신도
          함께 기다린다. 그리고 비결정론이면 같은 시뮬레이션을 다시 돌린 결과가 달라진다.

    :param as_of: 처리 기준일. **달력일**이다.
    :param inbound: `arrival.select_due_inbound` 이 `due` 로 가른 행.
    :param purchase_detail: 매입 원장이 확정한 사실 (품목·등급·수량·단가).
    """

    def provide(
        self,
        *,
        as_of: date,
        inbound: DueInbound,
        purchase_detail: PurchaseDetail,
    ) -> InspectionFact | None: ...


def format_block_reason(inbound_id: str | None, reason: InboundBlockReason) -> str:
    """막힌 사유 한 줄. **어느 건인지와 왜인지를 함께 남긴다.**

    ★ `inbound_id` 가 없는 것도 사실이다 (`ARRIVAL_INBOUND_ID_MISSING` 인 행). 빈
      문자열로 적으면 사유 목록에서 그 행이 사라진 것처럼 보인다.
    """
    return f"{inbound_id}: {reason}" if inbound_id else reason
