"""채팅 물류 보고서가 읽는 물류 사실 — 연결 하나 · 트랜잭션 하나로 물류 readmodel 을 차례로 부른다.

```text
기준일 재고 · 창고 사용량   get_inventory_console                        ← as_of 원장
입고 · 검수                 get_inbound_console
예약 · 출고                 get_outbound_console                         ← 같은 예약 한 벌
기간 재고 추이              onhand_total_by_day + snapshot_days_between
```

🔴 **커넥션은 한 보고서에 하나다.** `reservation_state_at` 도 한 번만 읽어 재고 콘솔과 출고 콘솔이
   나눠 쓴다 — 화면(`app/logistics/readmodel/console.read_console_page`)과 같은 조립 순서다.

★ 2026-09-30 재구성 BL-018: `master/report.py` 의 `render_logistics_chat_report` 앞머리(연결 대여와
  조회 일곱)를 옮겼다 — 블록 본문 · 경계(`core_db.connection` + `core_db.transaction`) · 순서
  그대로다. 문장 조립은 `report/chat_reports.py` 가 한다(보고서 계층은 연결을 빌리지 않는다).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.core import db as core_db
from app.logistics.readmodel.console import (
    get_inbound_console,
    get_inventory_console,
    get_outbound_console,
    load_console_runtime,
)
from app.logistics.readmodel.historical import (
    onhand_total_by_day,
    reservation_state_at,
    snapshot_days_between,
)
from app.logistics.schemas.console import (
    ConsoleInboundResponse,
    ConsoleInventoryResponse,
    ConsoleOutboundResponse,
)


@dataclass(frozen=True)
class LogisticsReportFacts:
    """보고서 한 판이 쓰는 물류 사실. 값은 물류 readmodel 이 낸 그대로다."""

    inventory: ConsoleInventoryResponse
    inbound: ConsoleInboundResponse
    outbound: ConsoleOutboundResponse
    #: 기간 안 날짜별 원장 누계 재고.
    series: dict[date, Decimal]
    #: 기간 안 시뮬레이션이 연 날.
    opened: frozenset[date]


def read_logistics_report_facts(
    *, sim_run_id: str, as_of: date, start_date: date, end_date: date
) -> LogisticsReportFacts:
    """물류 사실을 한 연결 · 한 트랜잭션에서 읽는다. **새 판정 · 새 SQL 0.**"""
    with core_db.connection() as conn, core_db.transaction(conn):
        runtime = load_console_runtime(conn=conn, sim_run_id=sim_run_id, as_of=as_of)
        reservations = reservation_state_at(conn, sim_run_id=sim_run_id, as_of=as_of)
        inventory = get_inventory_console(
            conn=conn,
            sim_run_id=sim_run_id,
            as_of=as_of,
            runtime=runtime,
            reservations=reservations,
        )
        inbound = get_inbound_console(
            conn=conn, sim_run_id=sim_run_id, as_of=as_of, runtime=runtime
        )
        outbound = get_outbound_console(
            conn=conn, sim_run_id=sim_run_id, as_of=as_of, reservations=reservations
        )
        series = onhand_total_by_day(conn, sim_run_id=sim_run_id, start=start_date, end=end_date)
        opened = snapshot_days_between(conn, sim_run_id=sim_run_id, start=start_date, end=end_date)
    return LogisticsReportFacts(
        inventory=inventory, inbound=inbound, outbound=outbound, series=series, opened=opened
    )
