"""승인 전이의 재무 원장 SQL — 매입채무 · 다음 상태."""

from __future__ import annotations

from decimal import Decimal

from psycopg import Connection, sql

from app.core.settings import get_db_schema
from app.finance.schemas.finance_state import H1_STATE_TYPE
from app.finance.schemas.transition import FinancePayableWrite, FinanceTransitionPlan


def insert_payable(conn: Connection[dict[str, object]], payable: FinancePayableWrite) -> int:
    """매입채무 한 행을 적는다 (같은 매입이면 적지 않는다). 적힌 행 수를 돌려준다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                    INSERT INTO {}.payables (
                        payable_id, sim_run_id, purchase_id, issued_date, due_date,
                        original_amount_krw, paid_amount_krw, outstanding_amount_krw, status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, 0, %s, 'OPEN')
                    ON CONFLICT (purchase_id) DO NOTHING
                    """
            ).format(schema),
            [
                payable.payable_id,
                payable.sim_run_id,
                payable.purchase_id,
                payable.issued_date,
                payable.due_date,
                payable.amount_krw,
                payable.amount_krw,
            ],
        )
        return cursor.rowcount


def upsert_transition_state(
    conn: Connection[dict[str, object]],
    transition: FinanceTransitionPlan,
    *,
    new_payables_krw: Decimal,
    inventory_book_value_krw: Decimal,
    operational_inventory_value_krw: Decimal,
) -> int:
    """승인 다음 날 상태를 세우거나 새 채무만 더한다. 바뀐 행 수를 돌려준다."""
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
                        %s, sim_run_id, %s, %s, financing_mode,
                        current_cash_krw, minimum_operating_cash_krw, committed_outflows_krw,
                        unsettled_purchase_payables_krw + %s, receivables_krw,
                        %s, %s,
                        current_debt_krw, recommended_loan_amount_krw, %s
                    FROM {schema}.finance_states
                    WHERE finance_state_id = %s
                    ON CONFLICT (sim_run_id, financing_mode, state_date) DO UPDATE SET
                        state_type = EXCLUDED.state_type,
                        unsettled_purchase_payables_krw =
                            current_state.unsettled_purchase_payables_krw + %s,
                        inventory_book_value_krw = EXCLUDED.inventory_book_value_krw,
                        operational_inventory_value_krw =
                            EXCLUDED.operational_inventory_value_krw,
                        note = EXCLUDED.note
                    """
            ).format(schema=schema),
            [
                transition.next_finance_state_id,
                transition.next_state_date,
                H1_STATE_TYPE,
                new_payables_krw,
                inventory_book_value_krw,
                operational_inventory_value_krw,
                "H1 승인 매입채무 반영",
                transition.source_finance_state_id,
                new_payables_krw,
            ],
        )
        return cursor.rowcount
