"""입고 일정 읽기 — 요청 범위 캐시(`schedule_view_scope`)와 그날 일정 보기 · 운송 중 · 도착 처리
대상 · 미래 입고.

★ 2026-09-30 재구성 BL-015: `logistics/inbound_schedules.py` 에서 옮겼다. SQL 은
  `repository/inbound_schedules.py`,
  종료조건은 `domain/inbound_schedules.py`. 화면은 이 파일의 `schedule_view_scope` 로 범위를 연다.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date
from typing import Any

from app.logistics.domain.historical import timestamp_cutoff
from app.logistics.domain.inbound_schedules import (
    in_transit_from,
    pending_inbound_from,
    receivable_from,
    reject_broken_reference,
)
from app.logistics.repository.inbound_schedules import select_schedule_view_rows
from app.logistics.schemas.inbound_schedules import InboundScheduleView
from app.logistics.schemas.snapshot import InTransitItem, ScheduledQuantity


class _ViewScope:
    """한 요청 안에서 `load_schedule_views` 가 낸 답을 들고 있는 자리.

    🔴 **열쇠마다 자물쇠가 하나씩 있다.** 한 화면이 물류를 **동시에** 두 갈래로 읽으면
       (대시보드가 그렇다) 둘이 같은 순간에 «없다» 를 보고 **둘 다 질의를 보낸다** —
       그러면 안 묶은 것과 같다. 먼저 온 쪽이 자물쇠를 잡고 읽고, 뒤에 온 쪽은 기다렸다
       담긴 답을 집는다.

    ⚠️ **답이 안 담기는 경우도 있다** — 먼저 온 쪽이 예외로 터진 때다. 그때 뒤 쪽은
       자기가 다시 읽는다. 실패를 담아 두면 한 번의 실패가 그 판 전체를 죽인다.
    """

    def __init__(self) -> None:
        self._answers: dict[tuple[str, date], tuple[InboundScheduleView, ...]] = {}
        self._guard = threading.Lock()
        self._locks: dict[tuple[str, date], threading.Lock] = {}

    def lock_for(self, key: tuple[str, date]) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def get(self, key: tuple[str, date]) -> tuple[InboundScheduleView, ...] | None:
        return self._answers.get(key)

    def put(self, key: tuple[str, date], views: tuple[InboundScheduleView, ...]) -> None:
        self._answers[key] = views


#: 한 요청 안에서 `load_schedule_views` 가 낸 답. **범위 밖에서는 `None` 이고,
#: 그때는 종전 그대로 매번 읽는다.**
_VIEW_SCOPE: ContextVar[_ViewScope | None] = ContextVar(
    "logistics_schedule_view_scope", default=None
)


@contextmanager
def schedule_view_scope() -> Iterator[None]:
    """이 블록 안에서 **같은 `(sim_run_id, as_of)` 일정 조회를 한 번만** 한다.

    🔴 **읽기 전용 한 판에만 쓴다.** 대시보드 화면이 물류를 두 갈래로 읽어
       (`logistics.query.build` · `logistics.query.dashboard_stock`) 같은 289행 질의가
       한 요청에 **두 번** 나갔다 (실측 2026-09-17 · 0.145s + 0.139s). 두 갈래는 같은
       날 · 같은 실행을 묻고 그 사이에 아무것도 쓰지 않으므로 답이 같다.

    ⚠️ **쓰는 흐름(도착 처리 · 승인 · 취소)을 이 범위로 감싸면 안 된다.** 취소가
       들어간 뒤에도 옛 목록이 나온다 — `inbound_reconciliation` 경로가 그렇다.

    ★ **스레드를 건너 나눠 쓸 수 있다.** `ContextVar` 는 새 스레드에 저절로 따라가지
      않으므로, 부르는 쪽이 `contextvars.copy_context()` 로 떠서 넘긴다 —
      `app/api/dashboard/presenter.py` 가 그렇게 한다. 나눠 쓰는 것은 **답(불변 튜플)**
      뿐이고 커넥션은 각자 자기 것을 쓴다.

    ★ 범위를 안 열면 아무것도 안 바뀐다.
    """
    token = _VIEW_SCOPE.set(_ViewScope())
    try:
        yield
    finally:
        _VIEW_SCOPE.reset(token)


def load_schedule_views(
    conn: Any, *, sim_run_id: str, as_of: date
) -> tuple[InboundScheduleView, ...]:
    """`as_of` 시점에 살아 있던 일정 + 그날까지의 계보. **한 질의다.**

    ★ `schedule_view_scope()` 안이면 같은 `(sim_run_id, as_of)` 는 **처음 한 번만**
      읽는다. 범위 밖이면 종전 그대로 매번 읽는다.

    ```text
    created_as_of <= as_of                              그날 이미 장부에 서 있었다
    cancelled_as_of IS NULL OR cancelled_as_of > as_of   그날 아직 취소 전이었다
    ```

    🔴 **계보도 `as_of` 로 자른다.** Receipt 는 `arrived_at <= as_of`, Lot 은
       `received_at <= as_of`, 원장 IN 은 `moved_at <= as_of` 다 — 오늘 상태를
       과거 날짜 답에 섞으면 Historical 조회가 거짓말을 한다.

       ★ **수용 0 완료는 검수 사건으로 자른다** (`inspected_at < timestamp_cutoff(as_of)`
         — `readmodel/historical.receipt_state_at` 의 INSPECTED 와 같은 규칙).
         `PUTAWAY_DONE` 에는 날짜가 없지만, 수용 0 이면 재고화가 쓰는 것이 Receipt
         상태뿐이고 그것이 검수 기록과 **한 트랜잭션**에서 선다
         (`inbound_execution._receive_one`). `receipt_status` 는 «재고화가 이미 돌았나»
         만 본다 — 되돌아가지 않는 상태라 과거 날짜를 앞당기지 않는다.

    🔴 **계보는 `EXISTS` 로 묻는다. `LEFT JOIN` 으로 끌어오지 않는다.**

    ```text
    Receipt 1 → Lot 2      LEFT JOIN 이면 일정 한 건이 2줄이 된다
    Lot 1 → IN Move 2      〃
    ```

       DDL 이 그 둘을 막지 않는다 — `inventory_lots.inbound_receipt_id` 에도
       `inventory_moves` 의 `(lot_id, move_type)` 에도 UNIQUE 가 없다. JOIN 곱으로
       늘어나면 `pending_inbound_at` 이 **같은 수량을 두 번 세어** Capacity 가
       틀린다. `EXISTS` 는 있고 없음만 묻고 행을 늘리지 않는다 —
       *"일정 1건 → Reader 1건"* 이 구조적으로 성립한다.

    🔴 **`purchase_items` 는 `LEFT JOIN` 하고 없으면 멈춘다.** 종전에는 `JOIN` 이라
       참조가 깨진 일정이 **결과에서 조용히 사라졌다**(실측 재현). 깨진 참조를
       *"입고 없음"* 으로 읽으면 그것이 곧 FIRSTINB 사고의 모양이다 —
       `ScheduleReferenceBroken` 으로 드러낸다.

    :raises ScheduleReferenceBroken: 일정의 매입 줄·품목 참조가 깨졌을 때.
    """
    scope = _VIEW_SCOPE.get()
    if scope is None:
        return _load_schedule_views(conn, sim_run_id=sim_run_id, as_of=as_of)
    key = (sim_run_id, as_of)
    #  ★ 자물쇠를 잡고 **다시 본다.** 기다리는 동안 앞사람이 담아 놨을 수 있다.
    with scope.lock_for(key):
        answer = scope.get(key)
        if answer is not None:
            return answer
        views = _load_schedule_views(conn, sim_run_id=sim_run_id, as_of=as_of)
        #  ★ 답이 선 **뒤에** 담는다 — 위에서 터지는 날에는 아무것도 안 담겨서,
        #    같은 범위의 다음 호출이 자기가 다시 읽는다.
        scope.put(key, views)
        return views


def _load_schedule_views(
    conn: Any, *, sim_run_id: str, as_of: date
) -> tuple[InboundScheduleView, ...]:
    """질의 한 번. **`load_schedule_views` 의 알맹이이고 범위를 모른다.**"""
    rows = select_schedule_view_rows(
        conn, sim_run_id=sim_run_id, as_of=as_of, cutoff=timestamp_cutoff(as_of)
    )
    reject_broken_reference(rows, sim_run_id=sim_run_id, as_of=as_of)
    views = tuple(
        InboundScheduleView(
            inbound_id=row["inbound_id"],
            sim_run_id=row["sim_run_id"],
            purchase_item_id=row["purchase_item_id"],
            purchase_id=row["purchase_id"],
            item_id=row["item_id"],
            item_name=row["item_name"],
            quantity_kg=row["quantity_kg"],
            expected_arrival_date=row["expected_arrival_date"],
            created_as_of=row["created_as_of"],
            has_receipt=bool(row["has_receipt"]),
            stock_applied=bool(row["stock_applied"]),
            settled_without_stock=bool(row["settled_without_stock"]),
        )
        for row in rows
    )
    return views


def in_transit_at(conn: Any, *, sim_run_id: str, as_of: date) -> list[InTransitItem]:
    """`load_schedule_views` + `in_transit_from`. 규칙은 저쪽에 있다."""
    return in_transit_from(load_schedule_views(conn, sim_run_id=sim_run_id, as_of=as_of))


def receivable_at(conn: Any, *, sim_run_id: str, as_of: date) -> list[InTransitItem]:
    """`load_schedule_views` + `receivable_from`. 규칙은 저쪽에 있다."""
    return receivable_from(load_schedule_views(conn, sim_run_id=sim_run_id, as_of=as_of))


def pending_inbound_at(conn: Any, *, sim_run_id: str, as_of: date) -> list[ScheduledQuantity]:
    """`load_schedule_views` + `pending_inbound_from`. 규칙은 저쪽에 있다."""
    return pending_inbound_from(load_schedule_views(conn, sim_run_id=sim_run_id, as_of=as_of))
