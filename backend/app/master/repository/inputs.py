"""마스터 입력 SQL — 예측 배치 · 확정 판매 · 파트너 일수요 · 주문 주기 (받은 연결, 읽기만).

조회 연결은 `readmodel/inputs.py` 가 SELECT 하나에 하나씩 빌린다. 행 → payload · 파생 계산은
`domain/inputs.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from psycopg import sql


def select_latest_forecast(
    conn: Any, *, item: str, as_of: date, target_kind: str, schema: str
) -> dict[str, Any] | None:
    """`as_of` 이하 최신 배치 한 행(`v_ml_price_forecast`). 당일인지는 부르는 쪽이 본다."""
    query = sql.SQL("""
        SELECT * FROM {}.v_ml_price_forecast
         WHERE item = %s AND as_of <= %s AND target_kind = %s
         ORDER BY as_of DESC
         LIMIT 1
    """).format(sql.Identifier(schema))
    with conn.cursor() as cursor:
        cursor.execute(query, (item, as_of, target_kind))
        row = cursor.fetchone()
    return dict(row) if row else None


def select_booked_order_rows(
    conn: Any, *, item: str, after: date, until: date, sim_run_id: str, schema: str
) -> list[dict[str, Any]]:
    """그 실행 축에서 `after` 초과 `until` 이하 납품일의 확정(`CONFIRMED` · `READY`) 판매 줄."""
    query = sql.SQL("""
        SELECT s.sale_id, s.sale_date, si.quantity_kg
          FROM {sch}.sales s
          JOIN {sch}.sale_items si ON si.sale_id = s.sale_id
          JOIN {sch}.items i ON i.item_id = si.item_id
         WHERE i.item_name = %s
           AND s.sale_date > %s
           AND s.sale_date <= %s
           AND s.order_status IN ('CONFIRMED', 'READY')
           AND s.sim_run_id = %s
         ORDER BY s.sale_date
    """).format(sch=sql.Identifier(schema))
    with conn.cursor() as cursor:
        cursor.execute(query, (item, after, until, sim_run_id))
        return cursor.fetchall()


def select_partner_demand(conn: Any, *, item: str, schema: str) -> dict[str, Any] | None:
    """그 품목의 파트너 일수요 한 행. 없으면 `None`."""
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL("""
            SELECT d.daily_demand_kg, d.demand_basis, d.provisional
              FROM {sch}.partner_item_demands d
              JOIN {sch}.items i ON i.item_id = d.item_id
             WHERE i.item_name = %s
        """).format(sch=sql.Identifier(schema)),
            (item,),
        )
        return cursor.fetchone()


def select_order_cycle(conn: Any, *, schema: str) -> dict[str, Any] | None:
    """현재 파트너 수요의 주문 주기 한 행. 없으면 `None`."""
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL("SELECT order_cycle_days FROM {sch}.v_current_partner_demand LIMIT 1").format(
                sch=sql.Identifier(schema)
            ),
            (),
        )
        return cursor.fetchone()


def select_item_daily_demands(
    conn: Any, *, items: Sequence[str], schema: str
) -> list[dict[str, Any]]:
    """주어진 품목들의 파트너 일수요(품목 이름 · kg). 분모를 좁히는 것은 `items` 다."""
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL("""
            SELECT i.item_name, d.daily_demand_kg
              FROM {sch}.partner_item_demands d
              JOIN {sch}.items i ON i.item_id = d.item_id
             WHERE i.item_name = ANY(%s)
        """).format(sch=sql.Identifier(schema)),
            (list(items),),
        )
        return cursor.fetchall()
