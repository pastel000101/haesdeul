"""입고 일정의 판정 — 소비자별 종료조건(운송 중 · 도착 처리 · Capacity) · 깨진 참조 · 같은 일정
재기록 · 취소를 적어야 하나. DB 를 만지지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from app.logistics.schemas.inbound_schedules import (
    InboundScheduleView,
    ScheduleAlreadyCancelled,
    ScheduleCancelConflict,
    ScheduleConflict,
    ScheduleReferenceBroken,
)
from app.logistics.schemas.snapshot import InTransitItem, ScheduledQuantity


def reject_broken_reference(
    rows: list[dict[str, Any]], *, sim_run_id: str, as_of: date
) -> None:
    """매입 줄·품목 참조가 깨진 일정이 있으면 멈춘다. 0건으로 답하지 않는다.

    어느 일정이 어느 참조를 잃었는지 적는다 — `purchase_item_id` 까지 보여야
    고칠 사람이 매입 쪽을 찾아갈 수 있다.
    """
    깨진것 = [
        f"{row['inbound_id']}→{row['purchase_item_id']}"
        for row in rows
        if row["purchase_id"] is None or row["item_id"] is None or row["item_name"] is None
    ]
    if 깨진것:
        raise ScheduleReferenceBroken(
            f"입고 일정이 가리키는 매입 줄·품목이 없다 (sim_run_id={sim_run_id!r},"
            f" as_of={as_of}): {sorted(깨진것)}."
            " 이 상태를 «입고 없음» 으로 읽지 않는다 —"
            " 승인은 났는데 도착 조회에 안 잡히는 입고가 되기 때문이다."
        )


#  ── 소비자별 종료조건 ────────────────────────────────────────────────────
#
#  세 Reader 는 같은 `load_schedule_views` 결과를 각자의 종료조건으로 거른 것이다.
#  그래서 거르는 규칙(`*_from`)과 읽기(`*_at`, `readmodel/inbound_schedules.py`)를 갈라
#  둔다 — 한 호출이 세 목록을 다 쓸 때 같은 질의를 세 번 보내지 않게
#  (2026-09-15 실측: 화면 한 번 그릴 때 5번 · 421 ms). 규칙의 주인은 이 파일이고, 부르는 쪽은
#  views 를 한 번 읽어 나눠 쓴다.


def in_transit_from(views: Sequence[InboundScheduleView]) -> list[InTransitItem]:
    """운송 중 — 아직 창고에 도착하지 않은 입고. `in_transit_at` 의 거르기 규칙.

    ```text
    종료조건   Receipt 가 생기면 빠진다
    ```

    ETA 가 지났다고 빼지 않는다. ETA 01-15 · 오늘 01-17 · Receipt 없음이면
    그것은 사라진 입고가 아니라 연체된 미도착이다
    (`arrival.select_due_inbound` 이 `overdue_count` 로 세는 그 상태다).
    """
    return [view.as_in_transit() for view in views if not view.has_receipt]


def _receiving_completed(view: InboundScheduleView) -> bool:
    """도착 처리 · 미래 점유의 종료조건. 재고가 섰거나, 수용 0 으로 처리가 끝났다.

    `Receipt 존재` 는 여기에 없다 — 처리 중(`ARRIVED` · `INSPECTED`)인 건은 끝난 것이 아니다.
    """
    return view.stock_applied or view.settled_without_stock


def receivable_from(views: Sequence[InboundScheduleView]) -> list[InTransitItem]:
    """도착 처리 대상 — 아직 재고가 서지 않은 입고. `receivable_at` 의 거르기 규칙.

    ```text
    종료조건   Lot 과 원장 IN 이 둘 다 서면 빠진다
               수용 0 으로 PUTAWAY_DONE · CLOSED 가 되면 빠진다 (만들 재고가 없다)
    ```

    Receipt 존재로 빼지 않는다. 검수에서 막힌 건(`Receipt=ARRIVED` · Lot 없음)은
    다음 실행이 이어받아야 한다 — `service/inbound_execution._receive_one` 이
    `check_receipt_state` 로 마지막 성공 단계 다음부터 잇는 구조라, 여기서 빼면
    그 입고가 영구 고착된다.

    날짜(`eta <= as_of`)로 자르지 않는다 — 그 판정은 `arrival.select_due_inbound` 이
    네 갈래(`due` · `blocked` · `not_due` · `unresolved`)로 나누며 소유한다.
    여기서 미리 자르면 "아직 안 온 것" 과 "못 받은 것" 이 구별되지 않는다.
    """
    return [view.as_in_transit() for view in views if not _receiving_completed(view)]


def pending_inbound_from(views: Sequence[InboundScheduleView]) -> list[ScheduledQuantity]:
    """미래 점유로 셀 입고 — Capacity 가 읽는 일정. `pending_inbound_at` 의 거르기 규칙.

    ```text
    종료조건   Lot 과 원장 IN 이 둘 다 서면 빠진다 (그때부터 on_hand 가 센다)
               수용 0 으로 PUTAWAY_DONE · CLOSED 가 되면 빠진다 (셀 재고가 없다)
    ```

    한 번만 계상하기 위한 경계다.

    ```text
    재고 반영 전   여기가 센다              on_hand 에는 없다
    재고 반영 후   여기서 빠진다            on_hand 가 센다
    ```

    Receipt 만 있고 Lot 이 없는 1,000kg 을 여기서 빼면 창고에 와 있는 물건이
    점유에서 사라져 없는 여유가 생긴다. 반대로 Lot 이 선 뒤에도 남기면
    같은 수량을 두 번 센다.

    `receivable_from` 과 같은 행을 고른다 — 다른 것은 DTO 모양뿐이다
    (`ScheduledQuantity.date` vs `InTransitItem.expected_arrival_date`).
    """
    return [view.as_scheduled_quantity() for view in views if not _receiving_completed(view)]


def schedule_already_recorded(
    기존: Mapping[str, Any] | None,
    *,
    sim_run_id: str,
    inbound_id: str,
    purchase_item_id: str,
    quantity_kg: Decimal,
    expected_arrival_date: date,
    created_as_of: date,
) -> bool:
    """같은 `(sim_run_id, inbound_id)` 일정이 같은 사실로 이미 있으면 참(더 쓸 것 없음).

    취소된 일정이거나 다른 사실이면 멈춘다. 없으면 거짓. DB 를 만지지 않는다.
    """
    if 기존 is not None:
        # 취소가 먼저다. 값이 같아도 되살리는 것은 별개의 결정이고,
        # 그 결정을 여기서 조용히 내리지 않는다.
        if 기존["cancelled_as_of"] is not None:
            raise ScheduleAlreadyCancelled(
                f"이미 취소된 입고 일정을 다시 적으려 한다 (sim_run_id={sim_run_id!r},"
                f" inbound_id={inbound_id!r}, cancelled_as_of={기존['cancelled_as_of']})."
                " 같은 승인을 다시 반영해도 취소를 무르지 않는다 —"
                " 취소를 되돌리는 업무 계약이 저장소에 없다."
                " 되살려야 한다면 그 근거를 먼저 정하고 이 자리를 고친다."
            )
        같음 = (
            기존["purchase_item_id"] == purchase_item_id
            and Decimal(str(기존["quantity_kg"])) == Decimal(str(quantity_kg))
            and 기존["expected_arrival_date"] == expected_arrival_date
            and 기존["created_as_of"] == created_as_of
        )
        if 같음:
            # 멱등: 같은 승인을 두 번 반영해도 행이 안 부푼다.
            return True
        # 어느 칸이 다른지 보이게 적는다. "다르다" 만으로는 고칠 사람이 못 찾는다.
        비교 = ("purchase_item_id", "quantity_kg", "expected_arrival_date", "created_as_of")
        이번값 = {
            "purchase_item_id": purchase_item_id,
            "quantity_kg": quantity_kg,
            "expected_arrival_date": expected_arrival_date,
            "created_as_of": created_as_of,
        }
        raise ScheduleConflict(
            f"같은 입고 일정이 다른 사실로 이미 있다 (sim_run_id={sim_run_id!r},"
            f" inbound_id={inbound_id!r})."
            f" 기존={ {칸: 기존[칸] for 칸 in 비교} } 이번={이번값}."
            " 덮지도 버리지도 않는다 — 어느 쪽이 진짜인지 여기서 고를 근거가 없다."
        )
    return False


def schedule_needs_cancel(
    기존: Mapping[str, Any] | None,
    *,
    sim_run_id: str,
    inbound_id: str,
    cancelled_as_of: date,
) -> bool:
    """이 일정에 취소를 새로 적어야 하나. 없거나 같은 날 이미 취소면 거짓, 다른 날이면 멈춘다.

    DB 를 만지지 않는다.
    """
    if 기존 is None:
        # Backfill 이전에 사라진 일정도 있을 수 있다. 없는 것을 걷어도 오류가
        # 아니다 — `service/cancellation.withdraw_inventory` 의 같은 태도다.
        return False
    이미 = 기존["cancelled_as_of"]
    if 이미 is not None:
        if 이미 == cancelled_as_of:
            return False
        raise ScheduleCancelConflict(
            f"이미 다른 날짜로 취소된 일정이다 (sim_run_id={sim_run_id!r},"
            f" inbound_id={inbound_id!r}, 기존 cancelled_as_of={이미},"
            f" 이번={cancelled_as_of}). 언제 취소됐나가 둘이 될 수 없다."
        )
    return True
