"""현금 투영이 읽는 확정 일정 SQL — 미결제 매입채무 · 일정 행 · 미지급 운영비."""

from datetime import date
from typing import Any

from psycopg import sql

from app.core.settings import get_db_schema
from app.finance.repository._cursor import fetch_all


def select_open_payables(
    conn: Any, *, sim_run_id: str, horizon_end: date
) -> list[dict[str, object]]:
    """``horizon_end`` 까지 기일이 오는(지난 것 포함) 미결제 매입채무."""
    query = sql.SQL(
        """
        SELECT payable_id, due_date, outstanding_amount_krw
        FROM {}.payables
        WHERE sim_run_id = %s
          AND due_date <= %s
          AND status = 'OPEN'
        ORDER BY due_date, payable_id
        """
    ).format(sql.Identifier(get_db_schema()))
    return fetch_all(conn, query, [sim_run_id, horizon_end])


def select_scheduled_rows(
    conn: Any,
    *,
    table: str,
    columns: tuple[str, str, str],
    sim_run_id: str,
    as_of: date,
    horizon_end: date,
    status_column: str,
    active_status: str | None = None,
    excluded_status: str | None = None,
) -> list[dict[str, object]]:
    """``(as_of, horizon_end]`` 에 날짜가 오는 일정 행 (채권 등)."""
    status_clause = sql.SQL("{} = %s").format(sql.Identifier(status_column))
    status_value = active_status
    if excluded_status is not None:
        status_clause = sql.SQL("{} <> %s").format(sql.Identifier(status_column))
        status_value = excluded_status
    assert status_value is not None
    query = sql.SQL(
        """
        SELECT {}, {}, {}
        FROM {}.{}
        WHERE sim_run_id = %s
          AND {} > %s
          AND {} <= %s
          AND {}
        ORDER BY {}, {}
        """
    ).format(
        *(sql.Identifier(column) for column in columns),
        sql.Identifier(get_db_schema()),
        sql.Identifier(table),
        sql.Identifier(columns[1]),
        sql.Identifier(columns[1]),
        status_clause,
        sql.Identifier(columns[1]),
        sql.Identifier(columns[0]),
    )
    return fetch_all(conn, query, [sim_run_id, as_of, horizon_end, status_value])


def select_accrued_expenses(
    conn: Any, *, sim_run_id: str, as_of: date, horizon_end: date
) -> list[dict[str, object]]:
    """아직 안 나간 운영비 의무. 미래 현금유출 투영이 읽는 자리다.

    `status = 'ACCRUED'` 를 직접 쓴다. `status <> 'PAID'` 로 거르면 취소된 비용이 의무로
    들어온다. 나가지 않기로 한 돈을 나갈 돈으로 세면 화면의 현금 여력이 실제보다 적어지고, 그
    숫자로 판매가 막힌다.

    기준일은 `due_date` 다 — 발생일이 아니다. 9월 16일에 생긴 임차료를 20일에 내기로 했으면
    현금은 20일에 빠진다.

    `due_date` 가 비어 있는 기존 행은 `expense_date` 로 읽는다. 이 칸이 생기기 전에 적힌
    `ACCRUED` 행이 있다면 그 의무는 조용히 사라지면 안 된다(LEGACY READ COMPATIBILITY ONLY).
    원장에 날짜를 채워 넣지는 않는다 — 신규 비용은 `due_date` 를 필수로 받으므로 이 경로는
    과거 데이터에만 닿는다.
    """
    query = sql.SQL(
        """
        SELECT expense_id,
               COALESCE(due_date, expense_date) AS effective_due_date,
               amount_krw
        FROM {}.expenses
        WHERE sim_run_id = %s
          AND status = 'ACCRUED'
          AND COALESCE(due_date, expense_date) > %s
          AND COALESCE(due_date, expense_date) <= %s
        ORDER BY COALESCE(due_date, expense_date), expense_id
        """
    ).format(sql.Identifier(get_db_schema()))
    return fetch_all(conn, query, [sim_run_id, as_of, horizon_end])
