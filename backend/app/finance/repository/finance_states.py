"""재무 상태(`finance_states` · `v_current_finance_state`) SQL — 축 · as-of 행 · 현재 행 ·
그날 행 잠금 · 현금 갱신.

그날 행 잠금 · 현금 갱신은 자금 조정과 운영비 지급이 같이 쓴다.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from psycopg import sql

from app.core.settings import get_db_schema
from app.finance.repository._cursor import fetch_all

_FINANCE_STATE_COLUMNS = (
    "finance_state_id",
    "sim_run_id",
    "state_date",
    "state_type",
    "financing_mode",
    "current_cash_krw",
    "minimum_operating_cash_krw",
    "committed_outflows_krw",
    "unsettled_purchase_payables_krw",
    "receivables_krw",
    "current_debt_krw",
    "financial_limit_krw",
)


def select_runtime_axis(conn: Any, *, sim_run_id: str | None = None) -> list[dict[str, object]]:
    """현재 재무 상태 View 에서 (실행 · 조달 방식) 축. 실행을 주면 그 실행만."""
    schema = sql.Identifier(get_db_schema())
    if sim_run_id is None:
        query = sql.SQL(
            "SELECT DISTINCT sim_run_id, financing_mode FROM {}.v_current_finance_state"
        ).format(schema)
        return fetch_all(conn, query)
    query = sql.SQL(
        """
        SELECT DISTINCT sim_run_id, financing_mode
        FROM {}.v_current_finance_state
        WHERE sim_run_id = %s
        """
    ).format(schema)
    return fetch_all(conn, query, [sim_run_id])


def select_state_rows_as_of(
    conn: Any, *, sim_run_id: str, financing_mode: str, as_of: date
) -> list[dict[str, object]]:
    """축 안에서 ``as_of`` 이전 가장 늦은 상태 두 행 (같은 날 둘인지 보려고)."""
    query = sql.SQL(
        """
        SELECT {}
        FROM {}.finance_states
        WHERE sim_run_id = %s
          AND financing_mode = %s
          AND state_date <= %s
        ORDER BY state_date DESC
        LIMIT 2
        """
    ).format(
        sql.SQL(", ").join(sql.Identifier(column) for column in _FINANCE_STATE_COLUMNS),
        sql.Identifier(get_db_schema()),
    )
    return fetch_all(conn, query, [sim_run_id, financing_mode, as_of])


def select_current_state_rows(
    conn: Any, *, sim_run_id: str | None = None
) -> list[dict[str, object]]:
    """현재 재무 상태 View 의 행. 실행을 주면 그 실행만."""
    schema = sql.Identifier(get_db_schema())
    columns = sql.SQL(", ").join(
        sql.Identifier(column) for column in _FINANCE_STATE_COLUMNS
    )
    if sim_run_id is None:
        query = sql.SQL("SELECT {} FROM {}.v_current_finance_state").format(
            columns, schema
        )
        return fetch_all(conn, query)
    query = sql.SQL(
        "SELECT {} FROM {}.v_current_finance_state WHERE sim_run_id = %s"
    ).format(columns, schema)
    return fetch_all(conn, query, [sim_run_id])


def lock_state_on_date(
    conn: Any, *, sim_run_id: str, financing_mode: str, state_date: date
) -> list:
    """축과 날짜가 정확히 맞는 재무 상태 행을 잠그고 읽는다 — 자금 조정 · 운영비 지급."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT finance_state_id, current_cash_krw
                FROM {}.finance_states
                WHERE sim_run_id = %s AND financing_mode = %s AND state_date = %s
                FOR UPDATE
                """
            ).format(schema),
            [sim_run_id, financing_mode, state_date],
        )
        return cursor.fetchall()


def update_state_cash(conn: Any, *, finance_state_id: object, current_cash: Decimal) -> int:
    """재무 상태 한 행의 현금을 바꾼다. 바뀐 행 수를 돌려준다 (자금 조정 · 운영비 지급 공용)."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "UPDATE {}.finance_states SET current_cash_krw = %s WHERE finance_state_id = %s"
            ).format(schema),
            [current_cash, finance_state_id],
        )
        return cursor.rowcount
