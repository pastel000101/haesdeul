"""그날 나갈 판매 줄 SQL — 확정 판매 · 판매 줄을 읽는다(받은 연결)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from psycopg import sql

from app.core.settings import get_db_schema
from app.master.repository.day_openings import handled_on_first_open_day
from app.master.schemas.outbound_flow import DueSaleItem


def due_sale_items(conn: Any, *, as_of: date, sim_run_id: str) -> tuple[DueSaleItem, ...]:
    """`as_of` 가 납품 기준일인 이 실행의 확정 판매 품목들.

    `sim_run_id` 로 거른다 (마스터 판단 2026-09-11). 축을 안 걸면 이 조회는 "남의
    실행" 이 아니라 모든 실행의 그 날짜 판매를 본다. 같은 날짜에 두 실행이 서는 순간
    남의 실행 판매가 내 창고에서 나가고, 나간 물건은 되돌릴 경로가 없어 두 실행의
    재고가 동시에 틀린다.

      `repository/sales_reads.py` 의 `read_confirmed_sales` 도 같은 판단을 따른다.
      거기서는 남의 축 판매가 `confirm_receivable` 의 conflict 를 불러 그날 전체를
      `BLOCKED` 로 만들고, 여기서는 물건이 나간다 — 뒤엣것이 더 나쁘다.

    `order_status` 가 `CONFIRMED` · `READY` 인 것만 본다. `DELIVERED` 는 이미 나갔고
    `CANCELLED` 는 나가면 안 된다 — `mark_sale_delivered` 가 받아 주는 상태와 같은 표다.

    납품 처리일은 납품일 당일, 휴장이면 그 뒤 첫 개장일이다. `handled_on_first_open_day`
    가 그 규칙의 한 자리다. 정확 일치(`sale_date = %s`)로 잡으면 걷기가 건너뛴
    토요일 납품이 다음 개장일에도 안 잡힌다 (2026-03-07 · 04-04 실측: 예약 6건이 할당 0
    으로 남았다). backorder 가 아니다 — 처리된 다음 날은 그 사이 개장 행이 서서 다시
    안 잡힌다.

    `due_today` 는 미래 날짜가 섞이지 않게 막는 한 줄이다. 휴장 판정은 개장 표를 봐야
    해서 조회가 한다.

    축은 다르다. `WHERE` 가 거르는 자리 그 자체다. 파이썬에서 다시 거르지 않는다 —
    파이썬에서 거르면 답은 맞아도 DB 가 남의 실행 행을 전부 읽어 오고, "조회가 정본"
    이 아니게 된다. `DueSaleItem.sim_run_id` 는 그래서 거르는 칸이 아니라 되짚기용
    사본이다.
    """
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT s.sale_id,
                       s.sim_run_id,
                       s.sale_date,
                       si.sale_item_id,
                       si.item_id,
                       si.quantity_kg
                  FROM {}.sales AS s
                  JOIN {}.sale_items AS si ON si.sale_id = s.sale_id
                 WHERE s.sim_run_id = %s
                   AND {}
                   AND s.order_status IN ('CONFIRMED', 'READY')
                 ORDER BY s.sale_id, si.sale_item_id
                """
            ).format(
                schema,
                schema,
                # 휴장일 납품은 그 뒤 첫 개장일에 한 번 잡는다 (실측 2026-03-07 · 04-04).
                # 정확 일치면 걷기가 건너뛴 토요일 납품이 영원히 안 나간다. backorder 아님.
                handled_on_first_open_day(
                    sale_date=sql.SQL("s.sale_date"), sim_run_id=sql.SQL("s.sim_run_id")
                ),
            ),
            [sim_run_id, as_of, as_of, as_of],
        )
        rows = cursor.fetchall()
    return tuple(
        DueSaleItem(
            sale_id=row["sale_id"],
            sale_item_id=row["sale_item_id"],
            item_id=row["item_id"],
            sim_run_id=row["sim_run_id"],
            quantity_kg=Decimal(row["quantity_kg"]),
            sale_date=row["sale_date"],
        )
        for row in rows
    )
