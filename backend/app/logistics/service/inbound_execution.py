"""도착 예정을 실제 입고로 실행한다 (3-B4-J).

이 파일에는 도착 처리 한 번의 순서(`receive_inbound` · `_receive_one`)가 있다. 등록소
표면은 `adapter.LogisticsInboundExecution` 이고 그 `receive` 가 이 함수를 부른다. 검수
사실 · provider 계약 · 막힘 어휘는 `domain/inbound_execution.py`, 예외는
`schemas/inbound_execution.py` 다.

```text
runtime in_transit
  → select_due_inbound            도착 자격 판정 (순수 계산)
  → fetch_purchase_detail          매입 줄 — 등급·단가의 권위 출처
  → check_receipt_state            어디서부터 이어갈지를 여는 열쇠
  → create_arrived_receipt         (없을 때만) ARRIVED
  → record_inspection              (검수 전일 때만) → INSPECTED
  → materialize_inspected_inbound  Lot · 원장 IN · PUTAWAY_DONE
  → RECEIVED · NOTHING_DUE · BLOCKED
```

이 파일에는 업무가 없다. WMS 규칙은 전부 위 모듈들이 이미 소유하고 있고, 여기서 하는
일은 순서와 분기와 어휘뿐이다 (`master/service/transition.apply_approval` 이 재무·물류를
감싸는 것, `inbound_stock` 이 Lot·원장을 감싸는 것과 같은 결).

재구현 금지 목록 — 아래는 전부 다른 모듈이 이미 한다.

  ```text
  도착일 규칙(<= as_of)        arrival.select_due_inbound
  매입 참조 해석               purchase_detail.fetch_purchase_detail
  Receipt 정체성 · 멱등        receipts (receipt_id 결정론 + advisory lock)
  검수 항등식 · 상태 마감      inspections.record_inspection
  Lot · 원장 IN · PUTAWAY_DONE inbound_stock.materialize_inspected_inbound
  ```

  여기에 SQL 이 한 줄도 없는 것이 그 규율의 증거다.

`ALREADY_EXISTS` 를 "다 됐다" 로 읽지 않는다.

  ```text
  Receipt 가 ARRIVED 로 있다 · 검수 없음 · Lot 없음 · 원장 IN 없음
  ⇒ 행은 있지만 재고는 안 들어왔다
  ```

  그래서 `check_receipt_state` 가 함께 주는 `receipt_status` 로 갈라 마지막 성공 단계
  다음부터 이어간다 (`_receive_one` 표 참조). 존재 여부만 보고 건너뛰면 Receipt 만 남고
  재고가 안 들어온 채 영구 고착된다.

검수 결과를 지어내지 않는다 — provider 를 주입받는다.

  저장소 어디에도 "자동 시뮬레이션에서 몇 %가 PASS 인가" 를 정한 규칙이 없다
  (`inspections.py` 모듈 docstring 의 실측). 그래서 이 파일은 판정도 수량도 검수자도
  검수시각도 만들지 않고, `InspectionProvider` 가 주는 사실을 그대로
  `record_inspection` 에 넘긴다.

  ```text
  자동 PASS                        하지 않는다
  accepted = ordered_qty_kg        하지 않는다
  inspector = "SYSTEM"             하지 않는다  저장소에 시스템 행위자 규약이 없다
  inspected_at = datetime.now()    하지 않는다  같은 실행을 다시 돌리면 값이 달라진다
  ```

  기본 provider 를 두지 않는다. 생성 인자를 필수로 두면 "검수 사실의 주인이 누구인가" 를
  등록소 조립(`app/master/registry/bootstrap.py`)에서 눈에 보이게 정하게 된다. 기본값을
  두면 그 기본값이 곧 업무 규칙이 되고, 아무도 그것을 정한 적이 없다.

실패 처리: BLOCKED 와 예외를 가른다.

  ```text
  BLOCKED   처리 대상은 있는데 권위 있는 입력이 없어 못 간다
            → 값으로 돌려준다. 다른 입고는 계속 처리한다
  예외      실행·무결성이 깨졌다
            → 밖으로 올린다. 마스터가 통째로 롤백하고 FAILED 로 만든다
  ```

  `except Exception` 으로 뭉뚱그리지 않는다. 무결성 위반을 BLOCKED 로 삼키면 "데이터를
  주세요" 로 나가고, 깨진 장부 위에서 다음 날이 계속 진행된다.

`FAILED` 를 물류가 만들지 않는다. `InboundPartOut.status` 어휘는
`RECEIVED · NOTHING_DUE · BLOCKED` 셋뿐이고, `FAILED` 는 파트가 예외를 올렸을 때
`master/service/inbound.receive_arrivals` 가 롤백과 함께 만든다. 마스터 계약은 마스터
것이다.

커밋도 롤백도 하지 않고 커넥션을 새로 열지 않는다. 커넥션은 마스터가 주고 커밋은
마스터가 한 번 한다 — 여기서 커밋하면 뒤이어 실패했을 때 반쪽만 들어온 입고가 남는다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.contracts.parts import InboundPartOut
from app.logistics.domain.arrival import DueInbound, select_due_inbound
from app.logistics.domain.inbound_execution import (
    InboundBlockReason,
    InspectionProvider,
    format_block_reason,
)
from app.logistics.repository.purchase_detail import fetch_purchase_detail
from app.logistics.schemas.inbound_execution import UnknownReceiptStage
from app.logistics.schemas.purchase_detail import PurchaseDetailAmbiguous, PurchaseDetailMissing
from app.logistics.schemas.receipts import ReceiptStatus
from app.logistics.schemas.vocabulary import RECEIPT_BEFORE_INSPECTION, RECEIPT_INSPECTION_SETTLED
from app.logistics.service.inbound_stock import (
    load_in_transit_for_receiving,
    materialize_inspected_inbound,
)
from app.logistics.service.inspections import record_inspection
from app.logistics.service.receipts import check_receipt_state, create_arrived_receipt

#: 이 파트 이름. `master/registry/inbound.py` 의 `PARTS` 값과 같아야 한다.
_PART = "logistics"


def receive_inbound(
    conn: Any,
    *,
    as_of: date,
    sim_run_id: str,
    inspection_provider: InspectionProvider,
    usage_scope: str,
) -> InboundPartOut:
    """`as_of` 까지 도착 자격을 얻은 입고를 전부 처리한다.

    ```text
    ① 그날 내 실행의 in_transit 을 잠그고 읽는다
    ② select_due_inbound 으로 네 갈래로 가른다   ← 순수 계산, 여기서 규칙을 안 만든다
    ③ due 를 한 건씩 처리한다                     ← 한 건이 막혀도 나머지는 계속 간다
    ④ 어휘를 취합한다
    ```

    당일만 처리하지 않는다. `select_due_inbound` 이 `eta <= as_of` 로 가르므로
    연체분(overdue)도 함께 온다 — 어느 날 이 실행이 돌지 않았어도 다음 날이 밀린 것을
    받는다. 그 판정을 여기서 다시 쓰지 않는다.

    `blocked` 가 하나라도 있으면 전체가 `BLOCKED` 다. 받은 것이 있어도 그렇다 — "받을 게
    있었는데 못 받았다" 가 "받았다" 보다 먼저 알려야 하는 사실이다
    (`master/service/inbound._aggregate` 가 파트 사이에서 하는 판단과 같다).

    `received` 에는 재고화까지 끝난 건만 담는다. Receipt 만 서고 검수에서 막힌 건은 담지
    않는다 — 담으면 "받았다" 가 거짓이 된다.

    :param conn: 마스터가 소유한 커넥션. commit · rollback · close 를 하지 않는다.
    :param as_of: 받는 날. 달력일이다 (토·일·공휴일 포함).
    """
    # ── ① 잠그고 읽는다 (도착 전역 → fixture 행 FOR UPDATE) ────────
    in_transit = load_in_transit_for_receiving(
        conn,
        sim_run_id=sim_run_id,
        as_of=as_of,
        usage_scope=usage_scope,
    )

    # ── ② 판정은 순수 계산이 한다 ─────────────────────────────────
    selection = select_due_inbound(in_transit, as_of=as_of)

    blocked_reasons: list[str] = []
    if selection.source_status == "UNRESOLVED":
        # 네 목록이 다 비지만 `CONFIRMED_ZERO` 와 다른 사실이다.
        blocked_reasons.append(format_block_reason(None, "IN_TRANSIT_UNRESOLVED"))
    for blocked in selection.blocked:
        # 사유를 전부 남긴다. 하나만 적으면 그것을 고친 뒤 또 막힌다.
        blocked_reasons.extend(
            format_block_reason(blocked.item.inbound_id, reason) for reason in blocked.reasons
        )
    for unresolved in selection.unresolved:
        blocked_reasons.append(
            format_block_reason(unresolved.item.inbound_id, unresolved.reason)
        )

    # ── ③ 한 건씩 — 막힌 건이 나머지를 세우지 않는다 ──────────────
    received: list[str] = []
    for inbound in selection.due:
        block_reason = _receive_one(
                conn,
                as_of=as_of,
                inbound=inbound,
                sim_run_id=sim_run_id,
                inspection_provider=inspection_provider,
                usage_scope=usage_scope,
            )
        if block_reason is None:
            received.append(inbound.inbound_id)
        else:
            blocked_reasons.append(format_block_reason(inbound.inbound_id, block_reason))

    # ── ④ 어휘 ────────────────────────────────────────────────────
    if blocked_reasons:
        return InboundPartOut(
            part=_PART,
            status="BLOCKED",
            reason="; ".join(blocked_reasons),
            # 막힌 것이 있어도 받은 것은 받은 것이다. 지우면 그 사실이 사라진다.
            received=received,
        )
    if received:
        return InboundPartOut(part=_PART, status="RECEIVED", received=received)
    return InboundPartOut(part=_PART, status="NOTHING_DUE")


def _receive_one(
    conn: Any,
    *,
    as_of: date,
    inbound: DueInbound,
    sim_run_id: str,
    inspection_provider: InspectionProvider,
    usage_scope: str,
) -> InboundBlockReason | None:
    """입고 한 건을 마지막 성공 단계 다음부터 이어 처리한다.

    ```text
    Receipt 상태            이 함수가 부르는 것
    ─────────────────────  ────────────────────────────────────────────────
    (없음)                 create_arrived_receipt → provider → record_inspection
                           → materialize
    ARRIVED · INSPECTING    provider → record_inspection → materialize
    INSPECTED               materialize                       provider 를 부르지 않는다
    PUTAWAY_DONE · CLOSED   materialize                       provider 를 부르지 않는다
                            (기존 Lot · Move 를 읽어서 검증하고 남은 일정만 걷는다)
    ```

    `materialize` 는 어느 경로에서도 부른다. `PUTAWAY_DONE` 이어도 일정이 안 걷힌 반쪽
    상태가 있을 수 있고, 그 마무리가 정확히 그 함수의 일이다. 재고를 다시 만들지는 않는다
    — 없으면 무결성 오류로 멈춘다.

    :returns: 막혔으면 그 사유, 끝까지 갔으면 `None`.
    """
    # ── 매입 참조 해석 ────────────────────────────────────────────
    try:
        purchase_detail = fetch_purchase_detail(conn, purchase_id=inbound.purchase_id)
    except PurchaseDetailMissing:
        # 부재다. 승인이 만든 매입 줄 없이 물건이 왔다는 뜻이라 진행할 수 없고,
        # 시세·평균원가로 대신 채우지 않는다.
        return "PURCHASE_DETAIL_MISSING"
    except PurchaseDetailAmbiguous:
        # 어느 줄이 이 입고의 것인지 고르지 않는다. 고른 단가가 로트 원가로 굳는다.
        return "PURCHASE_DETAIL_AMBIGUOUS"
    # `InvalidPurchaseIdentity` 는 잡지 않는다. `select_due_inbound` 이 빈 참조를 이미
    # `blocked` 로 걸렀으므로, 여기 오면 그것은 계약이 깨진 것이다.

    # ── 어디서부터 이어갈지 ───────────────────────────────────────
    existing = check_receipt_state(
        conn, sim_run_id=sim_run_id, inbound_id=inbound.inbound_id
    )
    if existing.status == "NEW":
        written = create_arrived_receipt(
            conn,
            sim_run_id=sim_run_id,
            inbound=inbound,
            purchase_detail=purchase_detail,
        )
        receipt_id = written.receipt_id
        receipt_status: ReceiptStatus = written.receipt_status
    else:
        # 타입 좁히기 — `ALREADY_EXISTS` 면 두 값이 다 있다 (`check_receipt_state` 이
        # 비거나 어휘 밖인 값을 이미 막는다).
        assert existing.receipt_id is not None
        assert existing.receipt_status is not None
        # DB 에 적힌 id 를 쓴다. 우리가 지은 값이 아니라 그 행이 진짜다.
        receipt_id = existing.receipt_id
        receipt_status = existing.receipt_status

    # ── 검수 — 검수 전 상태에서만 ─────────────────────────────────
    if receipt_status in RECEIPT_BEFORE_INSPECTION:
        fact = inspection_provider.provide(
            as_of=as_of, inbound=inbound, purchase_detail=purchase_detail
        )
        if fact is None:
            # 대신 만들지 않는다. 여기서 PASS 를 지어내면 그 수량이 그대로 가용재고가
            # 되고, 아무도 그 판정을 한 적이 없다.
            return "INSPECTION_FACT_UNAVAILABLE"
        # 받은 세 값을 그대로 넘긴다. 항등식·시간대·검수자 검증은 저쪽 일이다.
        record_inspection(
            conn,
            receipt_id=receipt_id,
            inspected_at=fact.inspected_at,
            inspector=fact.inspector,
            outcome=fact.outcome,
        )
    elif receipt_status not in RECEIPT_INSPECTION_SETTLED:
        # 아는 단계로 접어 읽지 않는다 — 어휘가 늘었다는 뜻이다.
        raise UnknownReceiptStage(
            f"Receipt 상태를 검수 전으로도 후로도 읽을 수 없다: {receipt_status!r}"
            f" (receipt_id={receipt_id!r}, inbound_id={inbound.inbound_id!r})."
            f" 검수 전: {sorted(RECEIPT_BEFORE_INSPECTION)}"
            f" · 검수 후: {sorted(RECEIPT_INSPECTION_SETTLED)}."
        )

    # ── 재고화 ────────────────────────────────────────────────────
    # Lot · 원장 IN · PUTAWAY_DONE 이 저 함수 안에 다 있다. 여기서 다시 조립하지 않는다.
    # 일정은 걷지 않는다 — 완료는 Lot + 원장 IN 으로 유도한다.
    materialize_inspected_inbound(
        conn,
        as_of=as_of,
        receipt_id=receipt_id,
        purchase_detail=purchase_detail,
        usage_scope=usage_scope,
    )
    return None
