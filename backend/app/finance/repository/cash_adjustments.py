"""자금 조정 원장 SQL.

★ 2026-09-29 재구성 BL-014: `finance/cash_adjustments.py` 에서 옮겼다(문면 그대로).
"""

from datetime import date
from decimal import Decimal

from psycopg import sql

from app.core.settings import get_db_schema


def insert_cash_adjustment(
    conn,
    *,
    cash_adjustment_id: str,
    sim_run_id: str,
    financing_mode: str,
    adjustment_date: date,
    direction: str,
    category: str,
    amount_krw: Decimal,
    source_ref: str,
    note: str | None,
    recorded_by: str,
) -> None:
    """자금 조정 원장에 한 행을 적는다 (불변 원장 — 덮어쓰지 않는다)."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                INSERT INTO {}.finance_cash_adjustments (
                    cash_adjustment_id, sim_run_id, financing_mode, adjustment_date,
                    direction, category, amount_krw, source_ref, note, recorded_by
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
            ).format(schema),
            [
                cash_adjustment_id,
                sim_run_id,
                financing_mode,
                adjustment_date,
                direction,
                category,
                amount_krw,
                source_ref,
                note,
                recorded_by,
            ],
        )
