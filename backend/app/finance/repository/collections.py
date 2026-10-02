"""누적 수금 SQL — 수금일 상태 잠금 · 채권 · 상태 갱신 · 사용자 수금 사건 기록."""

from datetime import date
from decimal import Decimal

from psycopg import Connection, sql

from app.core.settings import get_db_schema
from app.finance.schemas.collections import CollectionTransitionPlan


def lock_collection_state(
    conn: Connection[dict[str, object]],
    *,
    sim_run_id: str,
    financing_mode: str,
    collection_date: date,
) -> list:
    """수금일의 재무 상태 행을 잠그고 읽는다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT finance_state_id
                FROM {}.finance_states
                WHERE sim_run_id = %(sim_run_id)s
                  AND financing_mode = %(financing_mode)s
                  AND state_date = %(collection_date)s
                FOR UPDATE
                """
            ).format(schema),
            {
                "sim_run_id": sim_run_id,
                "financing_mode": financing_mode,
                "collection_date": collection_date,
            },
        )
        return cursor.fetchall()


def lock_finance_state(conn: Connection[dict[str, object]], *, finance_state_id: str):
    """재무 상태 한 행을 잠그고 읽는다. 없으면 `None`."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "SELECT * FROM {}.finance_states WHERE finance_state_id = %s FOR UPDATE"
            ).format(schema),
            [finance_state_id],
        )
        return cursor.fetchone()


def lock_receivable(conn: Connection[dict[str, object]], *, receivable_id: str):
    """채권 한 행을 잠그고 읽는다. 없으면 `None`."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL("SELECT * FROM {}.receivables WHERE receivable_id = %s FOR UPDATE").format(
                schema
            ),
            [receivable_id],
        )
        return cursor.fetchone()


def update_receivable_collection(
    conn: Connection[dict[str, object]], plan: CollectionTransitionPlan
) -> int:
    """채권 행에 누적 수금을 적는다. 바뀐 행 수를 돌려준다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """UPDATE {}.receivables
                   SET received_amount_krw = %s, outstanding_amount_krw = %s, status = %s
                   WHERE receivable_id = %s"""
            ).format(schema),
            [
                plan.target_received_total_krw,
                plan.next_outstanding_amount_krw,
                plan.next_status,
                plan.receivable_id,
            ],
        )
        return cursor.rowcount


def update_state_collection(
    conn: Connection[dict[str, object]], plan: CollectionTransitionPlan
) -> int:
    """재무 상태 행의 현금·채권을 수금만큼 옮긴다. 바뀐 행 수를 돌려준다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """UPDATE {}.finance_states
                   SET current_cash_krw = %s, receivables_krw = %s
                   WHERE finance_state_id = %s"""
            ).format(schema),
            [
                plan.next_current_cash_krw,
                plan.next_receivables_krw,
                plan.finance_state_id,
            ],
        )
        return cursor.rowcount


def lock_receivable_amounts(
    conn: Connection[dict[str, object]], *, receivable_id: str, sim_run_id: str
):
    """사용자 수금 기록 전에 그 채권의 원금 · 누적 수금을 잠그고 읽는다. 없으면 `None`."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL("""SELECT original_amount_krw, received_amount_krw FROM {}.receivables
                           WHERE receivable_id = %s AND sim_run_id = %s FOR UPDATE""").format(
                schema
            ),
            [receivable_id, sim_run_id],
        )
        return cursor.fetchone()


def insert_collection_event(
    conn: Connection[dict[str, object]],
    *,
    sim_run_id: str,
    financing_mode: str,
    collection_date: date,
    receivable_id: str,
    target_received_total_krw: Decimal,
    note: str,
) -> int:
    """사용자 수금 사건 한 행을 `master_collection_events` 에 적는다.

    같은 축 · 같은 날 · 같은 채권의 사건이 이미 있으면 적지 않는다.

    이 표의 주인은 마스터이지만, 사용자가 기록한 수금은 재무 수금 기록이 적는다 — 사건 기록과
    수금 적용이 한 트랜잭션이어야 해서다. 적힌 행 수를 돌려준다.
    """
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL("""INSERT INTO {}.master_collection_events
                           (sim_run_id, financing_mode, collection_date, receivable_id,
                            target_received_total_krw, note)
                           VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""").format(
                schema
            ),
            [
                sim_run_id,
                financing_mode,
                collection_date,
                receivable_id,
                target_received_total_krw,
                note,
            ],
        )
        return cursor.rowcount
