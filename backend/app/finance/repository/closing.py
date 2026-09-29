"""재무 일마감 SQL — 실행축 · 상태 · 원장 합계 · 채무 귀속 · 마감 행.

★ 2026-09-29 재구성 BL-014: `finance/closing.py` 에서 옮겼다(문면 그대로).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from psycopg import sql

from app.core.settings import get_db_schema

# ---------------------------------------------------------------------------
# SQL — 받은 연결로 실행만 한다. commit 하지 않는다.
# ---------------------------------------------------------------------------


def select_run_axis(conn: Any, *, sim_run_id: str) -> list:
    """이 실행의 기간 · 조달 축 · 설정을 `sim_runs` 한 행에서 읽는다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT period_start, period_end, financing_mode, config_json
                FROM {}.sim_runs
                WHERE sim_run_id = %s
                """
            ).format(schema),
            [sim_run_id],
        )
        return cursor.fetchall()


def select_baseline_state(conn: Any, *, finance_state_id: str) -> list:
    """물려받은 시작 상태 행을 `finance_state_id` 로만 읽는다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT sim_run_id, financing_mode, current_cash_krw, receivables_krw,
                       current_debt_krw
                FROM {}.finance_states
                WHERE finance_state_id = %s
                """
            ).format(schema),
            [finance_state_id],
        )
        return cursor.fetchall()


def select_exact_state(conn: Any, *, sim_run_id: str, financing_mode: str, as_of: date) -> list:
    """마감일 그날의 실행축 상태 행 (최대 두 행)."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT financing_mode, current_cash_krw, receivables_krw, current_debt_krw
                FROM {}.finance_states
                WHERE sim_run_id = %s
                  AND financing_mode = %s
                  AND state_date = %s
                LIMIT 2
                """
            ).format(schema),
            [sim_run_id, financing_mode, as_of],
        )
        return cursor.fetchall()


def select_prior_states(
    conn: Any, *, sim_run_id: str, financing_mode: str, as_of: date
) -> list:
    """마감일 **앞** 같은 축 상태를 늦은 날부터 두 행."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT state_date, financing_mode, current_cash_krw, receivables_krw,
                       current_debt_krw
                FROM {}.finance_states
                WHERE sim_run_id = %s
                  AND financing_mode = %s
                  AND state_date < %s
                ORDER BY state_date DESC
                LIMIT 2
                """
            ).format(schema),
            [sim_run_id, financing_mode, as_of],
        )
        return cursor.fetchall()


def select_receivables_issued(conn: Any, *, sim_run_id: str, as_of: date):
    """그날 발행된 채권 원금 합(한 행)."""
    return _sum_row(
        conn,
        """
        SELECT COALESCE(SUM(original_amount_krw), 0) AS amount
        FROM {schema}.receivables
        WHERE sim_run_id = %s AND issued_date = %s
        """,
        [sim_run_id, as_of],
    )


def select_receivables_outstanding(conn: Any, *, sim_run_id: str, as_of: date):
    """그날까지 발행된 채권의 미수 합(한 행)."""
    return _sum_row(
        conn,
        """
        SELECT COALESCE(SUM(outstanding_amount_krw), 0) AS amount
        FROM {schema}.receivables
        WHERE sim_run_id = %s AND issued_date <= %s
        """,
        [sim_run_id, as_of],
    )


def select_sales_recognized(conn: Any, *, sim_run_id: str, as_of: date):
    """오늘 판매와, 휴장 뒤 **첫 개장일**에 넘겨받은 판매만 인식한다.

    ``sales.sale_date``는 납품 원장 날짜이므로 바꾸지 않는다. Master #714가
    출고 처리에 쓰는 ``master_day_openings`` 정본을 같은 의미로 읽되, Master의
    내부 helper를 import하지 않는다. 이전 성공 개장 행이 하나라도 있으면 그
    휴장일 판매는 이미 처리 기회를 지났으므로 다음 마감에서 다시 인식하지 않는다.
    """
    return _sum_row(
        conn,
        """
        SELECT COALESCE(SUM(total_amount_krw), 0) AS amount
        FROM {schema}.sales AS s
        WHERE s.sim_run_id = %s
          AND (
                s.sale_date = %s
                OR (
                    s.sale_date < %s
                    AND NOT EXISTS (
                        SELECT 1
                        FROM {schema}.master_day_openings AS opening
                        WHERE opening.sim_run_id = s.sim_run_id
                          AND opening.as_of >= s.sale_date
                          AND opening.as_of < %s
                          AND opening.result IN ('OPENED', 'ALREADY_OPENED')
                    )
                )
          )
          AND s.order_status IN ('CONFIRMED', 'DELIVERED')
        """,
        [sim_run_id, as_of, as_of, as_of],
    )


def _sum_row(conn: Any, query: str, params: list[object]):
    with conn.cursor() as cursor:
        cursor.execute(sql.SQL(query).format(schema=sql.Identifier(get_db_schema())), params)
        return cursor.fetchone()


def select_unrecognized_due_payables(conn: Any, *, sim_run_id: str, as_of: date) -> list:
    """기일이 왔는데 아직 귀속 원장에 안 적힌 채무 — 기일 · id 순."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT p.payable_id, p.due_date, p.outstanding_amount_krw
                FROM {schema}.payables p
                WHERE p.sim_run_id = %s
                  AND p.issued_date <= %s
                  AND p.due_date <= %s
                  AND p.status IN ('OPEN', 'PARTIAL')
                  AND NOT EXISTS (
                      SELECT 1 FROM {schema}.finance_payable_closing_events e
                      WHERE e.sim_run_id = p.sim_run_id AND e.payable_id = p.payable_id
                  )
                ORDER BY p.due_date, p.payable_id
                """
            ).format(schema=schema),
            [sim_run_id, as_of, as_of],
        )
        return cursor.fetchall()


def insert_payable_recognition(
    conn: Any,
    *,
    sim_run_id: str,
    payable_id: str,
    recognized_date: date,
    recognized_amount_krw: Decimal,
    due_date: date,
) -> None:
    """채무 한 건을 그날 현금곡선에 실었다고 적는다 (이미 적혔으면 적지 않는다)."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                    INSERT INTO {}.finance_payable_closing_events (
                        sim_run_id, payable_id, recognized_date,
                        recognized_amount_krw, due_date
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (sim_run_id, payable_id) DO NOTHING
                    """
            ).format(schema),
            [
                sim_run_id,
                payable_id,
                recognized_date,
                recognized_amount_krw,
                due_date,
            ],
        )


def select_recognized_amounts(conn: Any, *, sim_run_id: str, as_of: date) -> list:
    """이 실행에서 이 날로 적힌 귀속 금액들."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT recognized_amount_krw
                FROM {}.finance_payable_closing_events
                WHERE sim_run_id = %s AND recognized_date = %s
                """
            ).format(schema),
            [sim_run_id, as_of],
        )
        return cursor.fetchall()


def select_paid_expenses(conn: Any, *, sim_run_id: str, as_of: date) -> list:
    """그날 지급된(`PAID`) 비용 행."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT expense_category, related_delivery_id, amount_krw,
                       status, paid_date, expense_date
                FROM {}.expenses
                WHERE sim_run_id = %s
                  AND status = 'PAID'
                  AND COALESCE(paid_date, expense_date) = %s
                """
            ).format(schema),
            [sim_run_id, as_of],
        )
        return cursor.fetchall()


def insert_daily_closing(conn: Any, params: dict[str, object]) -> int:
    """마감 한 행을 새로 적는다(이미 있으면 적지 않는다). **적힌 행 수.**"""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                INSERT INTO {schema}.daily_closings (
                    sim_run_id, close_date, day_no,
                    purchase_cash_out_krw, logistics_cash_out_krw,
                    payroll_interest_cash_out_krw, operating_expense_cash_out_krw,
                    sales_recognized_krw,
                    collection_cash_in_krw, base_net_cash_krw, base_cash_balance_krw,
                    loan_execution_krw, loan_cash_balance_krw, receivables_balance_krw,
                    inventory_qty_kg, accounting_inventory_cost_krw, closed
                ) VALUES (
                    %(sim_run_id)s, %(close_date)s, %(day_no)s,
                    %(purchase_cash_out_krw)s, %(logistics_cash_out_krw)s,
                    %(payroll_interest_cash_out_krw)s,
                    %(operating_expense_cash_out_krw)s, %(sales_recognized_krw)s,
                    %(collection_cash_in_krw)s, %(base_net_cash_krw)s,
                    %(base_cash_balance_krw)s, %(loan_execution_krw)s,
                    %(loan_cash_balance_krw)s, %(receivables_balance_krw)s,
                    %(inventory_qty_kg)s, %(accounting_inventory_cost_krw)s, TRUE
                ) ON CONFLICT (sim_run_id, close_date) DO NOTHING
                """
            ).format(schema=schema),
            params,
        )
        return cursor.rowcount


def update_daily_closing(conn: Any, params: dict[str, object]) -> int:
    """이미 있는 마감 행을 다시 적는다. **바뀐 행 수.**"""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                UPDATE {schema}.daily_closings
                SET day_no = %(day_no)s,
                    purchase_cash_out_krw = %(purchase_cash_out_krw)s,
                    logistics_cash_out_krw = %(logistics_cash_out_krw)s,
                    payroll_interest_cash_out_krw = %(payroll_interest_cash_out_krw)s,
                    operating_expense_cash_out_krw = %(operating_expense_cash_out_krw)s,
                    sales_recognized_krw = %(sales_recognized_krw)s,
                    collection_cash_in_krw = %(collection_cash_in_krw)s,
                    base_net_cash_krw = %(base_net_cash_krw)s,
                    base_cash_balance_krw = %(base_cash_balance_krw)s,
                    loan_execution_krw = %(loan_execution_krw)s,
                    loan_cash_balance_krw = %(loan_cash_balance_krw)s,
                    receivables_balance_krw = %(receivables_balance_krw)s,
                    inventory_qty_kg = %(inventory_qty_kg)s,
                    accounting_inventory_cost_krw = %(accounting_inventory_cost_krw)s,
                    closed = TRUE
                WHERE sim_run_id = %(sim_run_id)s AND close_date = %(close_date)s
                """
            ).format(schema=schema),
            params,
        )
        return cursor.rowcount
