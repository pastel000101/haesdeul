"""입고 일정 Entity · 일정 보기(그날까지의 계보) · 실패 종류.

SQL 은 `repository/inbound_schedules.py`, 소비자별 종료조건은 `domain/inbound_schedules.py`,
요청 범위 캐시와 읽기는 `readmodel/inbound_schedules.py`, 기록 · 취소 순서는
`service/inbound_schedules.py` 에 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.logistics.schemas.snapshot import InTransitItem, ScheduledQuantity


class ScheduleConflict(ValueError):
    """같은 `(sim_run_id, inbound_id)` 가 다른 사실로 이미 있다. 무결성 위반이다.

    기존 행을 UPDATE 해서 맞추지 않는다. 어느 쪽이 진짜인지 여기서 고를 근거가
    없고, 고치면 그 순간 과거 사실이 조용히 바뀐다
    (`schemas/transition.InboundScheduleConflict` 와 같은 규율).
    """


class ScheduleAlreadyCancelled(ValueError):
    """취소된 일정을 같은 승인으로 다시 적으려 한다.

    조용히 되살리지 않는다. 대조 넷(`purchase_item_id` · `quantity_kg` ·
    `expected_arrival_date` · `created_as_of`)이 같아도, 이미 "그날부터 없다" 고
    적힌 행을 no-op 으로 넘기면 되살리는 결정을 아무도 내리지 않은 채 그 일정이
    다시 사는 것처럼 읽힌다.

    ```text
    승인 → 취소 → 같은 승인 재실행 → 여기서 멈춘다
    ```

    취소 이력을 지워 `cancelled_as_of = NULL` 로 되돌리지 않는다. 그것이
    옳으려면 "취소를 무를 수 있다" 는 업무 계약이 있어야 하는데, 저장소 어디에도
    그 계약이 없다 — 마스터 `undo_approval` 은 취소만 있고 되돌리기가 없다.
    근거 없이 과거 취소 이력을 지우는 쪽이 조용히 되살리는 것보다 더 위험하다.

    예외라서 바깥 트랜잭션이 통째로 롤백된다. 그것이 이 예외가 지키는 것이다.
    """


class ScheduleReferenceBroken(RuntimeError):
    """일정은 있는데 그 `purchase_item_id` 가 가리키는 매입 줄(또는 품목)이 없다.

    «입고 없음» 으로 읽지 않는다. `purchase_items` 를 INNER JOIN 하면 그 일정이
    결과에서 통째로 사라진다 — 승인은 났는데 도착 조회에 안 잡히는 상태이고,
    그것이 정확히 FIRSTINB 사고의 모양이다. 그래서 Reader 는 LEFT JOIN 으로 읽고
    깨진 참조를 만나면 멈춘다.

    ```text
    참조 정상   일정이 나온다
    참조 깨짐   0건으로 조용히 사라지지 않고 여기서 멈춘다
    ```

    `purchase_item_id` 에 FK 를 아직 안 걸었기 때문에 생기는 자리다
    (`purchase_items → purchases` 가 `ON DELETE CASCADE` 라, FK 를 걸면 매입 삭제가
    과거 재현용 일정까지 지운다 — 그 정책이 미정이다). DB 가 못 막는 동안
    Reader 가 막는다.
    """


class ScheduleCancelConflict(ValueError):
    """이미 다른 날짜로 취소된 일정을 또 다른 날짜로 취소하려 한다.

    같은 날짜로 다시 취소하는 것은 정상 재시도라 no-op 이다. 다른 날짜는
    "언제 취소됐나" 가 둘이 되는 것이라 막는다.
    """


class ScheduleReceiptExists(ValueError):
    """도착 Receipt 가 이미 있는 입고를 취소하려 한다.

    물건이 도착했으면 취소가 아니라 다른 업무 흐름이다 (반품 · 폐기 · 실사).
    `service/reconciliation` 도 같은 선을 긋는다 — Receipt 가 하나라도 있으면 거부한다.

    거부는 값이 아니라 예외다. 마스터 `undo_approval` 이 파트 거절을 받으면
    전이 전체를 롤백하므로, 매입·재무만 취소되고 물류만 남는 반쪽 상태가
    생기지 않는다.
    """


class ScheduleReferenceMissing(ValueError):
    """`purchase_id` 가 없어 `purchase_item_id` 를 세울 수 없다.

    비워 두고 넘어가지 않는다. 이 표가 입고 예정의 정본이라 빠진 행은
    "승인은 났는데 도착 조회에 안 잡히는 입고" 가 된다 — 그것이 정확히 FIRSTINB
    사고의 모양이다. 조용히 빼면 그 사고를 다시 심는 셈이다.

    마스터 승인 전이는 `purchase_ids` 를 넘긴다(`#311`) — 실측 in_transit 5/5 가
    `PUR-…` 값을 갖고 있어 정상 경로에서는 뜨지 않는다.
    """


@dataclass(frozen=True)
class InboundSchedule:
    """입고 예정 한 건. 그날 살아 있었나는 두 날짜가 답한다."""

    inbound_id: str
    sim_run_id: str
    purchase_item_id: str
    quantity_kg: Decimal
    expected_arrival_date: date
    created_as_of: date
    cancelled_as_of: date | None
    source_ref: str
    note: str | None


# ── W3-2 Reader — 소비자마다 종료조건이 다르다 ──────────────────────────
#
# Entity 하나 ≠ Reader 종료조건 하나. 이것이 이 절의 전부다.
#
# ```text
# 상태                        운송 중   도착 처리   Capacity
# ETA 전 · Receipt 없음          O        X          O
# ETA 도달 · Receipt 없음        O        O          O
# Receipt 있음 · Lot 없음        X        O          O    ← 여기가 갈리는 자리다
# Lot + IN Move 완료             X        X          X    (그때부터 on_hand 가 센다)
# 수용 0 검수 · PUTAWAY_DONE     X        X          X    (만들 재고가 없다 · 검수일부터)
# 취소됨                        X        X          X
# ```
#
# Receipt 가 생겼다고 모든 Reader 에서 빼지 않는다. 검수가 막히면 Receipt 만
# 선 채 며칠 간다(`inbound_execution._receive_one` 의 `INSPECTION_FACT_UNAVAILABLE`).
# 그 물건은 창고에 와 있고(→ Capacity 계상), 다음 실행이 이어받아야 하며(→ 도착
# 처리 대상), 다만 "운송 중" 은 아니다.
#
# `in_transit` 과 `confirmed_inbound` 이 같아야 한다는 불변조건을 만들지 않는다.
# 두 목록은 종료조건이 달라 `in_transit ⊆ confirmed_inbound` 다 — B-1 은 그 방향만
# 보므로 통과한다.


@dataclass(frozen=True)
class InboundScheduleView:
    """일정 한 건 + 그날까지의 입고 계보. 종료조건 판정은 소비자가 한다.

    `purchase_id` · `item_id` · `item_name` 을 표에 저장하지 않고 JOIN 으로 얻는다
    (`purchase_items` · `items` 가 그 값의 주인이다). 복사해 두면 매입이 값을 고치는
    날 일정만 옛 값을 들고 남는다.
    """

    inbound_id: str
    sim_run_id: str
    purchase_item_id: str
    purchase_id: str
    item_id: str
    item_name: str
    quantity_kg: Decimal
    expected_arrival_date: date
    created_as_of: date
    #: 그날까지 도착 Receipt 가 있었나 (`arrived_at <= as_of`).
    has_receipt: bool
    #: 그날까지 재고가 실제로 섰나 — Lot 과 원장 IN 이 둘 다 있어야 참이다.
    #: Receipt 존재로 대신하지 않는다. 그 둘은 다른 사건이다.
    stock_applied: bool
    #: 그날까지 수용 0 으로 입고 처리가 끝났나 — 수용 0 검수가 그날까지 있었고
    #: (`inspected_at < (as_of+1) 00:00 KST`) Receipt 가 `PUTAWAY_DONE` · `CLOSED` 다.
    #: 재고가 선 것이 아니다 — `stock_applied` 와 다른 사실이다.
    settled_without_stock: bool = False

    def as_in_transit(self) -> InTransitItem:
        """Legacy 계약 그대로의 운송 중 한 줄. DTO 를 새로 만들지 않는다."""
        return InTransitItem(
            inbound_id=self.inbound_id,
            purchase_id=self.purchase_id,
            item=self.item_name,
            quantity_kg=self.quantity_kg,
            expected_arrival_date=self.expected_arrival_date,
        )

    def as_scheduled_quantity(self) -> ScheduledQuantity:
        """Capacity 가 읽는 일정 한 줄. 필드 이름이 다르다 (`date`)."""
        return ScheduledQuantity(
            inbound_id=self.inbound_id,
            item=self.item_name,
            quantity_kg=self.quantity_kg,
            date=self.expected_arrival_date,
        )
