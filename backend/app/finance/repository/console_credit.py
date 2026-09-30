"""운영 콘솔 거래처 여신 SQL.

★ 2026-09-29 재구성 BL-014: `finance/console_credit.py` 에서 옮겼다(문면 그대로).
"""

from datetime import date
from typing import Any

from psycopg import sql

from app.core.settings import get_db_schema
from app.finance.repository._cursor import fetch_all
from app.finance.repository.receivable_history import history_columns, history_join


def load_credit_partner_rows(
    conn: Any, *, sim_run_id: str, as_of: date
) -> list[dict[str, object]]:
    """여신을 보여 줄 거래처. **한도가 서 있거나, 이 실행에서 판 적이 있는 고객이다.**"""
    schema = sql.Identifier(get_db_schema())
    query = sql.SQL(
        """
        SELECT p.partner_id, p.partner_name, p.sales_collection_days,
               (
                   SELECT l.evidence_grade
                   FROM {schema}.partner_credit_limits l
                   WHERE l.partner_id = p.partner_id
                     AND l.is_active
                     AND l.effective_from <= %s
                     AND (l.effective_to IS NULL OR l.effective_to >= %s)
                   ORDER BY l.effective_from DESC
                   LIMIT 1
               ) AS credit_limit_evidence_grade
        FROM {schema}.partners p
        WHERE EXISTS (
                  SELECT 1 FROM {schema}.partner_credit_limits l
                  WHERE l.partner_id = p.partner_id
                    AND l.is_active
                    AND l.effective_from <= %s
                    AND (l.effective_to IS NULL OR l.effective_to >= %s)
              )
           OR EXISTS (
                  SELECT 1 FROM {schema}.sales s
                  WHERE s.sim_run_id = %s
                    AND s.customer_partner_id = p.partner_id
                    AND s.sale_date <= %s
              )
        ORDER BY p.partner_id
        """
    ).format(schema=schema)
    return fetch_all(conn, query, [as_of, as_of, as_of, as_of, sim_run_id, as_of])


def select_partner_receivables_as_of(
    conn: Any, *, sim_run_id: str, as_of: date, partner_id: str
) -> list:
    """거래처 채권 — 판매일 · 발행일을 기준일로 막고 수금 칸을 기준일 시점으로 복원한다."""
    schema = get_db_schema()
    query = (
        sql.SQL(
            """
        SELECT r.receivable_id, r.due_date, r.original_amount_krw,
        """
        )
        + history_columns()
        + sql.SQL(
            """
        FROM {}.receivables r
        JOIN {}.sales s ON s.sale_id = r.sale_id AND s.sim_run_id = r.sim_run_id
        """
        ).format(sql.Identifier(schema), sql.Identifier(schema))
        + history_join(schema)
        + sql.SQL(
            """
        WHERE r.sim_run_id = %s
          AND s.customer_partner_id = %s
          AND r.issued_date <= %s
          AND s.sale_date <= %s
        ORDER BY r.due_date ASC, r.receivable_id ASC
        """
        )
    )
    #  ⚠️ `%s` 는 넷이다 — LATERAL 의 기준일이 WHERE 보다 **먼저** 온다.
    return fetch_all(conn, query, [as_of, sim_run_id, partner_id, as_of, as_of])
