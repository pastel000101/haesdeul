"""채권 발행 대상 판매 SQL — 확정 판매를 읽는다(받은 연결)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from psycopg import sql

from app.core.settings import get_db_schema
from app.master.repository.day_openings import handled_on_first_open_day

#: 채권을 세울 판매 상태. `DELIVERED` 가 들어 있는 것이 계약이다.
#:
#: 상수로 둔 이유는 검사가 이 값을 읽기 때문이다. SQL 문자열 안에만 있으면
#:   `DELIVERED` 가 빠진 날을 실 DB 없이는 아무도 못 잡는다.
ISSUABLE_ORDER_STATUSES: tuple[str, ...] = ("CONFIRMED", "READY", "DELIVERED")


@dataclass(frozen=True)
class ConfirmedSale:
    """그날 확정된 판매 한 줄. 판매가 소유한 사실을 읽어 온 것뿐이다.

    `collection_due_date` 가 `date | None` 인 것이 계약이다. `None` 은 "기일을 모른다" 이고
    0 도 오늘도 아니다 — 여기서 기본값을 채우면 마스터가 결제조건을 발명하는 것이 된다.
    """

    sale_id: str
    sim_run_id: str
    sale_date: date
    customer_partner_id: str
    collection_due_date: date | None
    total_amount_krw: Decimal


def read_confirmed_sales(
    conn: Any, *, as_of: date, sim_run_id: str
) -> tuple[ConfirmedSale, ...]:
    """`as_of` 가 `sale_date` 인 이 실행의 확정 판매. `DELIVERED` 도 대상이다.

    `sim_run_id` 로 거른다 (마스터 판단 2026-09-09). 안 거르면 남의 실행 판매가 같은
    `sale_date` 에 들어왔을 때 `confirm_receivable` 이 conflict 를 내고 그날 전체가
    `BLOCKED` 가 된다. 남의 축 판매는 "못 만든 것" 이 아니라 애초에 내 대상이 아니다 — 그
    둘을 한 값으로 접으면 막힌 날을 나중에 설명할 수 없다.

    마스터 커넥션으로 읽는다. 채권을 쓰는 트랜잭션과 같은 커넥션이라야, 읽은 판매와 쓴
    채권이 같은 스냅샷 위에 선다 (`repository/outbound_flow.due_sale_items` 와 같은
    모양이다).

    실패 처리: 못 읽으면 예외를 그대로 올린다. `()` 로 접으면 "오늘 확정된 판매가 없다"
    와 구별할 수 없다. 접는 판단은 부르는 쪽 몫이다.
    """
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT sale_id,
                       sim_run_id,
                       sale_date,
                       customer_partner_id,
                       collection_due_date,
                       total_amount_krw
                  FROM {}.sales
                 WHERE sale_date <= %s
                   AND sim_run_id = %s
                   AND order_status = ANY(%s)
                   AND {}
                 ORDER BY sale_id
                """
            ).format(
                schema,
                # 출고와 같은 규칙이다 — 휴장일 납품은 그 뒤 첫 개장일에 한 번 발행한다.
                # 실측 2026-03-07 · 04-04 · MISSING_RECEIVABLE 6. backorder 아님.
                handled_on_first_open_day(
                    sale_date=sql.SQL("sales.sale_date"), sim_run_id=sql.SQL("sales.sim_run_id")
                ),
            ),
            [as_of, sim_run_id, list(ISSUABLE_ORDER_STATUSES), as_of, as_of, as_of],
        )
        rows = cursor.fetchall()
    return tuple(
        ConfirmedSale(
            sale_id=row["sale_id"],
            sim_run_id=row["sim_run_id"],
            sale_date=row["sale_date"],
            customer_partner_id=row["customer_partner_id"],
            collection_due_date=row["collection_due_date"],
            total_amount_krw=Decimal(row["total_amount_krw"]),
        )
        for row in rows
    )
