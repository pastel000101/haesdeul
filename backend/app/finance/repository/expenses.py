"""일반 운영비 SQL.

★ 2026-09-29 재구성 BL-014: `finance/expenses.py` 에서 옮겼다(문면 그대로).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from psycopg import sql

from app.core.settings import get_db_schema


def insert_expense(
    conn: Any,
    *,
    expense_id: str,
    sim_run_id: str,
    expense_date: date,
    due_date: date,
    expense_category: str,
    amount_krw: Decimal,
    is_fixed: bool,
    related_delivery_id: str | None,
    evidence_id: str,
    note: str | None,
) -> int:
    """`ACCRUED` 비용 한 행을 적는다. **적힌 행 수**를 돌려준다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                INSERT INTO {}.expenses (
                    expense_id, sim_run_id, expense_date, due_date, paid_date,
                    expense_category, amount_krw, is_fixed, related_delivery_id,
                    evidence_id, status, note
                ) VALUES (%s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, 'ACCRUED', %s)
                """
            ).format(schema),
            [
                expense_id,
                sim_run_id,
                expense_date,
                due_date,
                expense_category,
                amount_krw,
                is_fixed,
                related_delivery_id,
                evidence_id,
                note,
            ],
        )
        return cursor.rowcount


def lock_expense_for_settlement(conn: Any, *, expense_id: str) -> list:
    """지급할 비용 행을 잠그고 읽는다 (**잠그고 나서 읽는다**)."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT expense_id, sim_run_id, status, amount_krw
                FROM {}.expenses
                WHERE expense_id = %s
                FOR UPDATE
                """
            ).format(schema),
            [expense_id],
        )
        return cursor.fetchall()


def mark_expense_paid(conn: Any, *, expense_id: str, paid_date: date) -> int:
    """`ACCRUED` 비용을 `PAID` 로 적는다. **바뀐 행 수**를 돌려준다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                UPDATE {}.expenses
                SET status = 'PAID', paid_date = %s
                WHERE expense_id = %s AND status = 'ACCRUED'
                """
            ).format(schema),
            [paid_date, expense_id],
        )
        return cursor.rowcount


def select_run_financing_mode(conn: Any, *, sim_run_id: str) -> list:
    """이 실행의 `sim_runs` 행에서 조달 축을 읽는다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT financing_mode
                FROM {}.sim_runs
                WHERE sim_run_id = %s
                """
            ).format(schema),
            [sim_run_id],
        )
        return cursor.fetchall()


def select_due_expense_ids(conn: Any, *, sim_run_id: str, as_of: date) -> list[str]:
    """이 실행에서 `as_of` 까지 지급일이 된 `ACCRUED` 비용 id — `due_date, expense_id` 순."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        #  🔴 **이 실행의 `ACCRUED` 만, 지급일이 지난 것만.** `PAID` 를 다시 집으면 두 번
        #     나가고, 다른 축을 집으면 남의 현금이 준다.
        #
        #  ★ 정렬은 계약이다(`ORDER BY due_date, expense_id`). 현금이 모자라 중간에서
        #    막히는 날, **어느 것까지 나갔는가가 실행마다 달라지면** 같은 입력이 다른
        #    장부를 만든다.
        cursor.execute(
            sql.SQL(
                """
                SELECT expense_id
                FROM {}.expenses
                WHERE sim_run_id = %s
                  AND status = 'ACCRUED'
                  AND due_date <= %s
                ORDER BY due_date, expense_id
                """
            ).format(schema),
            [sim_run_id, as_of],
        )
        return [str(row["expense_id"]) for row in cursor.fetchall()]


def lock_expense_for_cancel(conn: Any, *, expense_id: str) -> list:
    """취소할 비용 행을 잠그고 읽는다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT expense_id, sim_run_id, status
                FROM {}.expenses
                WHERE expense_id = %s
                FOR UPDATE
                """
            ).format(schema),
            [expense_id],
        )
        return cursor.fetchall()


def mark_expense_cancelled(conn: Any, *, expense_id: str) -> int:
    """`ACCRUED` 비용을 `CANCELLED` 로 적는다. **바뀐 행 수**를 돌려준다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                UPDATE {}.expenses SET status = 'CANCELLED'
                WHERE expense_id = %s AND status = 'ACCRUED'
                """
            ).format(schema),
            [expense_id],
        )
        return cursor.rowcount
