"""매입대금 지급 SQL."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from psycopg import sql

from app.core.settings import get_db_schema
from app.finance.schemas.settlement import PayableSettlement

#: 아직 낼 돈이 남아 있는 채무 상태.
_PAYABLE_OPEN_STATUSES = ["OPEN", "PARTIAL"]


def lock_recognized_payables(conn: Any, *, sim_run_id: str, as_of: date) -> list:
    """그날 인식된 채무 중 낼 돈이 남은 것을 잠그고 읽는다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT p.payable_id, p.paid_amount_krw, p.outstanding_amount_krw,
                       e.recognized_amount_krw
                FROM {schema}.finance_payable_closing_events e
                JOIN {schema}.payables p
                  ON p.payable_id = e.payable_id AND p.sim_run_id = e.sim_run_id
                WHERE e.sim_run_id = %s
                  AND e.recognized_date = %s
                  AND p.status = ANY(%s)
                  AND p.outstanding_amount_krw > 0
                ORDER BY p.payable_id
                FOR UPDATE OF p
                """
            ).format(schema=schema),
            [sim_run_id, as_of, _PAYABLE_OPEN_STATUSES],
        )
        return cursor.fetchall()


def update_payable_settlement(conn: Any, settlement: PayableSettlement, *, as_of: date) -> int:
    """채무 한 행에 지급을 적는다. 바뀐 행 수를 돌려준다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                UPDATE {}.payables
                   SET paid_amount_krw = %s,
                       outstanding_amount_krw = %s,
                       status = %s,
                       settled_date = %s
                 WHERE payable_id = %s
                """
            ).format(schema),
            [
                settlement.next_paid_amount_krw,
                settlement.next_outstanding_amount_krw,
                settlement.next_status,
                # 다 갚은 날만 적는다. 부분 지급은 아직 «끝난 날» 이 아니다.
                as_of if settlement.next_status == "SETTLED" else None,
                settlement.payable_id,
            ],
        )
        return cursor.rowcount


def lock_settlement_state(conn: Any, *, sim_run_id: str, as_of: date) -> list:
    """그날 재무 상태 행을 잠그고 읽는다 (축이 둘이면 둘 다 나온다)."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT finance_state_id, current_cash_krw, unsettled_purchase_payables_krw
                FROM {}.finance_states
                WHERE sim_run_id = %s AND state_date = %s
                ORDER BY finance_state_id
                FOR UPDATE
                """
            ).format(schema),
            [sim_run_id, as_of],
        )
        return cursor.fetchall()


def update_state_payment(
    conn: Any,
    *,
    finance_state_id: object,
    current_cash_krw: Decimal,
    unsettled_purchase_payables_krw: Decimal,
) -> int:
    """재무 상태 행의 현금·미지급 채무를 바꾼다. 바뀐 행 수를 돌려준다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                UPDATE {}.finance_states
                   SET current_cash_krw = %s,
                       unsettled_purchase_payables_krw = %s
                 WHERE finance_state_id = %s
                """
            ).format(schema),
            [current_cash_krw, unsettled_purchase_payables_krw, finance_state_id],
        )
        return cursor.rowcount
