"""미지급 매입채무 취소 SQL."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from psycopg import sql

from app.core.settings import get_db_schema
from app.finance.schemas.finance_state import CANCELLATION_STATE_TYPE


def lock_payables(conn: Any, *, purchase_ids: list[str]) -> list:
    """취소할 매입의 채무 행을 잠그고 읽는다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(_locked_payables_query(schema), {"purchase_ids": purchase_ids})
        return cursor.fetchall()


def lock_cancellation_state(
    conn: Any, *, sim_run_id: str, financing_mode: str, state_date: date
) -> list:
    """그날 재무 상태 행을 잠그고 읽는다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            _lock_cancellation_state_query(schema, lock="UPDATE"),
            {
                "sim_run_id": sim_run_id,
                "financing_mode": financing_mode,
                "state_date": state_date,
            },
        )
        return cursor.fetchall()


def cancel_payables(conn: Any, *, purchase_ids: list[str], cancelled_date: date) -> list:
    """`OPEN` 채무를 취소로 적고 바뀐 행(매입 id · 취소 금액)을 돌려준다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            _cancel_payables_query(schema),
            {
                "purchase_ids": purchase_ids,
                "cancelled_date": cancelled_date,
            },
        )
        return cursor.fetchall()


def subtract_existing_state(
    conn: Any,
    *,
    finance_state_id: str,
    cancelled_amount: Decimal,
    inventory_book_value_krw: Decimal,
    operational_inventory_value_krw: Decimal,
) -> list:
    """있는 상태 행에서 취소분을 뺀다. 갱신된 상태 id 행을 돌려준다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
            UPDATE {}.finance_states
            SET unsettled_purchase_payables_krw =
                    unsettled_purchase_payables_krw - %(cancelled_amount)s,
                inventory_book_value_krw = %(inventory_book_value_krw)s,
                operational_inventory_value_krw = %(operational_inventory_value_krw)s
            WHERE finance_state_id = %(finance_state_id)s
              AND unsettled_purchase_payables_krw >= %(cancelled_amount)s
            RETURNING finance_state_id
            """
            ).format(schema),
            {
                "finance_state_id": finance_state_id,
                "cancelled_amount": cancelled_amount,
                "inventory_book_value_krw": inventory_book_value_krw,
                "operational_inventory_value_krw": operational_inventory_value_krw,
            },
        )
        return cursor.fetchall()


def carry_and_subtract_state(
    conn: Any,
    *,
    finance_state_id: str,
    source_finance_state_id: str,
    sim_run_id: str,
    financing_mode: str,
    as_of: date,
    target_state_date: date,
    cancelled_amount: Decimal,
    inventory_book_value_krw: Decimal,
    operational_inventory_value_krw: Decimal,
) -> list:
    """취소일 상태를 물려받아 세우면서 취소분을 뺀다. 세운 상태 id 행을 돌려준다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
            INSERT INTO {schema}.finance_states AS current_state (
                finance_state_id, sim_run_id, state_date, state_type, financing_mode,
                current_cash_krw, minimum_operating_cash_krw, committed_outflows_krw,
                unsettled_purchase_payables_krw, receivables_krw,
                inventory_book_value_krw, operational_inventory_value_krw,
                current_debt_krw, recommended_loan_amount_krw, note
            )
            SELECT
                %(finance_state_id)s, source.sim_run_id, %(target_state_date)s,
                %(state_type)s, %(financing_mode)s,
                source.current_cash_krw, source.minimum_operating_cash_krw,
                source.committed_outflows_krw,
                source.unsettled_purchase_payables_krw - %(cancelled_amount)s,
                source.receivables_krw, %(inventory_book_value_krw)s,
                %(operational_inventory_value_krw)s, source.current_debt_krw,
                source.recommended_loan_amount_krw, %(note)s
            FROM {schema}.finance_states source
            WHERE source.finance_state_id = %(source_finance_state_id)s
              AND source.sim_run_id = %(sim_run_id)s
              AND source.financing_mode = %(financing_mode)s
              AND source.state_date = %(as_of)s
              AND source.unsettled_purchase_payables_krw >= %(cancelled_amount)s
            ON CONFLICT (sim_run_id, financing_mode, state_date) DO UPDATE SET
                unsettled_purchase_payables_krw =
                    current_state.unsettled_purchase_payables_krw - %(cancelled_amount)s,
                inventory_book_value_krw = %(inventory_book_value_krw)s,
                operational_inventory_value_krw = %(operational_inventory_value_krw)s
            WHERE current_state.unsettled_purchase_payables_krw >= %(cancelled_amount)s
            RETURNING finance_state_id
            """
            ).format(schema=schema),
            {
                "finance_state_id": finance_state_id,
                "sim_run_id": sim_run_id,
                "financing_mode": financing_mode,
                "target_state_date": target_state_date,
                "state_type": CANCELLATION_STATE_TYPE,
                "cancelled_amount": cancelled_amount,
                "inventory_book_value_krw": inventory_book_value_krw,
                "operational_inventory_value_krw": operational_inventory_value_krw,
                "note": f"{as_of} 미지급 매입채무 취소 반영",
                "source_finance_state_id": source_finance_state_id,
                "as_of": as_of,
            },
        )
        return cursor.fetchall()


def _locked_payables_query(schema: sql.Identifier) -> sql.Composed:
    return sql.SQL(
        """
        SELECT
            purchase_id, sim_run_id, original_amount_krw, paid_amount_krw,
            cancelled_amount_krw, outstanding_amount_krw, status
        FROM {}.payables
        WHERE purchase_id = ANY(%(purchase_ids)s)
        ORDER BY purchase_id
        FOR UPDATE
        """
    ).format(schema)


def _cancel_payables_query(schema: sql.Identifier) -> sql.Composed:
    return sql.SQL(
        """
        UPDATE {}.payables
        SET cancelled_amount_krw = outstanding_amount_krw,
            outstanding_amount_krw = 0,
            status = 'CANCELLED',
            cancelled_date = %(cancelled_date)s
        WHERE purchase_id = ANY(%(purchase_ids)s)
          AND status = 'OPEN'
          AND paid_amount_krw = 0
        RETURNING purchase_id, cancelled_amount_krw
        """
    ).format(schema)


def _lock_cancellation_state_query(schema: sql.Identifier, *, lock: str) -> sql.Composed:
    if lock != "UPDATE":
        raise ValueError("unsupported Finance state lock")
    return sql.SQL(
        """
        SELECT finance_state_id, financing_mode, unsettled_purchase_payables_krw
        FROM {}.finance_states
        WHERE sim_run_id = %(sim_run_id)s
          AND financing_mode = %(financing_mode)s
          AND state_date = %(state_date)s
        FOR UPDATE
        """
    ).format(schema)
