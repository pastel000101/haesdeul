"""판매 확정분 → 매출채권 SQL.

★ 2026-09-29 재구성 BL-014: `finance/receivables.py` 에서 옮겼다(문면 그대로).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from psycopg import sql

from app.core.settings import get_db_schema
from app.finance.schemas.receivables import ReceivableWritePlan


def select_sale_rows(conn: Any, *, sale_id: str) -> list:
    """판매 헤더를 읽는다 (최대 두 행 — 한 행인지는 부르는 쪽이 본다)."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT
                    sale_id, sim_run_id, customer_partner_id, order_date, sale_date,
                    collection_due_date, total_quantity_kg, total_amount_krw,
                    contribution_profit_krw, collection_status, source_order_id, note,
                    order_status
                FROM {}.sales
                WHERE sale_id = %s
                LIMIT 2
                """
            ).format(schema),
            [sale_id],
        )
        return cursor.fetchall()


def lock_receivable_state(
    conn: Any, *, sim_run_id: str, financing_mode: str, state_date: date
) -> list:
    """발행일의 재무 상태 행을 잠그고 읽는다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT finance_state_id
                FROM {}.finance_states
                WHERE sim_run_id = %s
                  AND financing_mode = %s
                  AND state_date = %s
                FOR UPDATE
                """
            ).format(schema),
            [sim_run_id, financing_mode, state_date],
        )
        return cursor.fetchall()


def lock_state_for_receivable(conn: Any, *, finance_state_id: str) -> list:
    """채권을 얹을 재무 상태 행을 잠그고 읽는다 (최대 두 행)."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT
                    finance_state_id, receivables_krw
                FROM {}.finance_states
                WHERE finance_state_id = %s
                LIMIT 2
                FOR UPDATE
                """
            ).format(schema),
            [finance_state_id],
        )
        return cursor.fetchall()


def insert_receivable(conn: Any, plan: ReceivableWritePlan) -> int:
    """채권 한 행을 적는다 (같은 판매면 적지 않는다). **적힌 행 수**를 돌려준다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                INSERT INTO {}.receivables (
                    receivable_id, sim_run_id, sale_id, issued_date, due_date,
                    original_amount_krw, received_amount_krw, outstanding_amount_krw, status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (sale_id) DO NOTHING
                """
            ).format(schema),
            [
                plan.receivable_id,
                plan.sim_run_id,
                plan.sale_id,
                plan.issued_date,
                plan.due_date,
                plan.original_amount_krw,
                plan.received_amount_krw,
                plan.outstanding_amount_krw,
                plan.status,
            ],
        )
        return int(cursor.rowcount)


def add_state_receivables(conn: Any, *, finance_state_id: str, delta: Decimal) -> int:
    """재무 상태 행의 채권 잔액에 더한다. **바뀐 행 수**를 돌려준다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                UPDATE {}.finance_states
                SET receivables_krw = receivables_krw + %s
                WHERE finance_state_id = %s
                """
            ).format(schema),
            [delta, finance_state_id],
        )
        return int(cursor.rowcount)


def select_receivables_by_sale(conn: Any, *, sale_id: str) -> list:
    """한 판매의 채권 행을 읽는다 (최대 두 행)."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT *
                FROM {}.receivables
                WHERE sale_id = %s
                LIMIT 2
                """
            ).format(schema),
            [sale_id],
        )
        return cursor.fetchall()
