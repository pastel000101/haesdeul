"""입고 일정 기록 · 취소의 순서 — 기존 일정 잠금 → 같은 사실인가 → INSERT/UPDATE.

판정은 `domain/inbound_schedules.py`, SQL 은 `repository/inbound_schedules.py` 다. 부르는
곳은 승인 전이(`service/transition.py`) · 취소(`service/cancellation.py`) · 고착 정리
(`service/reconciliation.py`)이고, 모두 마스터가 준 연결로 실행한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from app.logistics.domain.inbound_schedules import schedule_already_recorded, schedule_needs_cancel
from app.logistics.repository.inbound_schedules import (
    has_receipt,
    insert_schedule,
    mark_schedule_cancelled,
    select_schedule,
)
from app.logistics.schemas.inbound_schedules import ScheduleReceiptExists


def record_schedule(
    conn: Any,
    *,
    sim_run_id: str,
    inbound_id: str,
    purchase_item_id: str,
    quantity_kg: Decimal,
    expected_arrival_date: date,
    created_as_of: date,
    source_ref: str,
    note: str | None = None,
) -> bool:
    """입고 예정 한 건을 적는다. 같은 사실이면 no-op, 다른 사실이면 멈춘다.

    ```text
    없음                 INSERT                     → True
    같은 사실 · 살아있음   아무것도 안 한다            → False   같은 승인 재반영이 여기다
    취소된 일정           ScheduleAlreadyCancelled   되살리지 않는다
    다른 사실            ScheduleConflict           덮지 않는다
    ```

    취소 여부를 대조 넷보다 먼저 본다. 값이 같아도 그 행은 이미 "그날부터 없다" 고 적힌
    행이다 (`ScheduleAlreadyCancelled` 참조).

    대조 대상 넷이 계약이다 — `purchase_item_id` · `quantity_kg` ·
    `expected_arrival_date` · `created_as_of`. `source_ref` · `note` 는 근거 기록이라
    대조에서 뺀다(같은 사실을 다른 경로로 다시 적을 수 있다).

    `quantity_kg` 를 `purchase_items.quantity_kg` 와 같게 강제하지 않는다. 이 값은 회차
    수량이고 매입 줄은 그 회차들의 합일 수 있다 — 실측 5/5 가 같은 것은 지금 분할 회차가
    없어서지 계약이 아니다. 없는 규칙을 만들지 않는다.

    :returns: 이번 호출이 실제로 행을 만들었나.
    :raises ScheduleAlreadyCancelled: 그 일정이 이미 취소돼 있을 때.
    :raises ScheduleConflict: 같은 열쇠가 다른 사실로 이미 있을 때.
    """
    기존 = select_schedule(conn, sim_run_id=sim_run_id, inbound_id=inbound_id)
    if schedule_already_recorded(
        기존,
        sim_run_id=sim_run_id,
        inbound_id=inbound_id,
        purchase_item_id=purchase_item_id,
        quantity_kg=quantity_kg,
        expected_arrival_date=expected_arrival_date,
        created_as_of=created_as_of,
    ):
        return False

    insert_schedule(
        conn,
        inbound_id=inbound_id,
        sim_run_id=sim_run_id,
        purchase_item_id=purchase_item_id,
        quantity_kg=quantity_kg,
        expected_arrival_date=expected_arrival_date,
        created_as_of=created_as_of,
        source_ref=source_ref,
        note=note,
    )
    return True


def cancel_schedule(
    conn: Any,
    *,
    sim_run_id: str,
    inbound_id: str,
    cancelled_as_of: date,
    cancel_source_ref: str | None = None,
) -> bool:
    """입고 예정을 그날부터 취소한다. 과거는 고치지 않는다.

    ```text
    as_of <  cancelled_as_of   그날 이 일정은 여전히 존재한다
    as_of >= cancelled_as_of   그날부터 취소다
    ```

    `cancel_source_ref` 는 취소 근거다. `source_ref`(생성 근거)를 덮지 않는다. 주지 않으면
    `NULL` 로 남고 취소 자체는 그대로 된다 — 그날 살아 있었나는 `cancelled_as_of` 하나가
    답한다.

    `cancelled_on` 이 아니라 `target_state_date` 를 받는다. 계약이 "01-07 취소 → 01-08
    상태에서 제거" 이고, 취소일 자체를 적으면 이미 지나간 하루의 사실이 바뀐다. 승인이
    `commitment.as_of + 1` 행에 서는 것과 같은 결이다.

    ```text
    없음                 아무것도 안 한다        → False   오류가 아니다
    아직 안 취소         NULL → cancelled_as_of  → True
    같은 날짜로 취소됨    아무것도 안 한다        → False   멱등 재시도
    다른 날짜로 취소됨    ScheduleCancelConflict
    ```

    Receipt 가 있으면 취소하지 않는다. 그 판정은 호출부(`withdraw_inventory`)가
    `assert_cancellable` 로 쓰기 전에 한다 — 한쪽만 바뀌는 상태를 만들지 않기 위해서다.
    이 함수는 그 뒤에 불린다.

    :returns: 이번 호출이 실제로 취소를 적었나.
    :raises ScheduleCancelConflict: 이미 다른 날짜로 취소돼 있을 때.
    """
    기존 = select_schedule(conn, sim_run_id=sim_run_id, inbound_id=inbound_id)
    if not schedule_needs_cancel(
        기존, sim_run_id=sim_run_id, inbound_id=inbound_id, cancelled_as_of=cancelled_as_of
    ):
        return False

    mark_schedule_cancelled(
        conn,
        sim_run_id=sim_run_id,
        inbound_id=inbound_id,
        cancelled_as_of=cancelled_as_of,
        cancel_source_ref=cancel_source_ref,
    )
    return True


def assert_cancellable(
    conn: Any, *, sim_run_id: str, inbound_ids: Sequence[str]
) -> None:
    """취소해도 되는 입고들인가. 취소 UPDATE 를 쓰기 전에 먼저 묻는다.

    한쪽만 바뀌는 상태를 만들지 않으려고 앞에서 전부 본다. 하나씩 걷다가 중간에 막히면
    앞의 것은 이미 닫힌 뒤다 — 같은 트랜잭션이라 롤백은 되지만, 판정이 쓰기와 섞이면
    "무엇이 왜 막혔나" 가 흐려진다.

    :raises ScheduleReceiptExists: 하나라도 Receipt 가 있을 때. 어느 것인지 적는다.
    """
    도착함 = [
        inbound_id
        for inbound_id in inbound_ids
        if inbound_id and has_receipt(conn, sim_run_id=sim_run_id, inbound_id=inbound_id)
    ]
    if 도착함:
        raise ScheduleReceiptExists(
            f"도착 Receipt 가 이미 있는 입고는 취소할 수 없다 (sim_run_id={sim_run_id!r}):"
            f" {sorted(도착함)}. 물건이 도착했으면 취소가 아니라 반품·폐기·실사이고,"
            " 그 판단은 여기서 대신 내리지 않는다."
        )
