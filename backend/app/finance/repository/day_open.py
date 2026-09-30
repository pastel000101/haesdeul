"""재무 개장 SQL.

★ 2026-09-29 재구성 BL-014: `finance/day_open.py` 에서 옮겼다(문면 그대로).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from psycopg import sql

from app.core.settings import get_db_schema


def select_day_open_axis(conn: Any, *, sim_run_id: str | None) -> list:
    """현재 재무 상태 View 에서 축(실행 · 조달 방식)을 읽는다. 실행을 주면 그 실행만."""
    schema = sql.Identifier(get_db_schema())
    if sim_run_id is None:
        query = sql.SQL(
            "SELECT DISTINCT sim_run_id, financing_mode"
            " FROM {}.v_current_finance_state"
        ).format(schema)
        params: list[object] = []
    else:
        query = sql.SQL(
            "SELECT DISTINCT sim_run_id, financing_mode"
            " FROM {}.v_current_finance_state WHERE sim_run_id = %s"
        ).format(schema)
        params = [sim_run_id]
    with conn.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()


def select_exact_states(
    conn: Any, *, sim_run_id: str, financing_mode: str, state_date: date
) -> list:
    """축과 날짜가 정확히 맞는 상태 행 (최대 두 행)."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            _state_ids_on_date_query(schema),
            {
                "sim_run_id": sim_run_id,
                "financing_mode": financing_mode,
                "state_date": state_date,
            },
        )
        return cursor.fetchall()


def carry_forward_state(conn: Any, params: dict[str, object]) -> int:
    """앞날 상태를 그날로 물려받아 한 행 세운다(이미 있으면 안 세운다). **세운 행 수.**"""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(_carry_forward_query(schema), params)
        return cursor.rowcount


def _state_ids_on_date_query(schema: sql.Identifier) -> sql.Composed:
    return sql.SQL(
        """
            SELECT finance_state_id
            FROM {}.finance_states
            WHERE sim_run_id = %(sim_run_id)s
              AND financing_mode = %(financing_mode)s
              AND state_date = %(state_date)s
            LIMIT 2
            """
    ).format(schema)


def _carry_forward_query(schema: sql.Identifier) -> sql.Composed:
    return sql.SQL(
        """
            INSERT INTO {schema}.finance_states (
                finance_state_id, sim_run_id, state_date, state_type, financing_mode,
                current_cash_krw, minimum_operating_cash_krw, committed_outflows_krw,
                unsettled_purchase_payables_krw, receivables_krw,
                inventory_book_value_krw, operational_inventory_value_krw,
                current_debt_krw, recommended_loan_amount_krw, note
            )
            SELECT
                %(finance_state_id)s, base.sim_run_id, %(as_of)s, %(state_type)s,
                base.financing_mode,
                base.current_cash_krw, base.minimum_operating_cash_krw,
                base.committed_outflows_krw, base.unsettled_purchase_payables_krw,
                base.receivables_krw, %(inventory_book_value_krw)s,
                %(operational_inventory_value_krw)s, base.current_debt_krw,
                base.recommended_loan_amount_krw, %(note)s
            FROM {schema}.finance_states base
            WHERE base.sim_run_id = %(sim_run_id)s
              AND base.financing_mode = %(financing_mode)s
              AND base.state_date = %(carry_from)s
            ON CONFLICT (finance_state_id) DO NOTHING
            """
    ).format(schema=schema)
