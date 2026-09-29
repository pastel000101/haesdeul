"""거래처 여신한도 SQL — 거래처 확인 · 활성 기간 잠금 · 기간 끝내기 · 새 기간 · 이력.

★ 2026-09-29 재구성 BL-014: `finance/router.py` 핸들러 안의 SQL 을 옮겼다(문면 · 인자 그대로 —
  거래처 확인 한 문장만 등록 · 조회가 같이 쓰도록 조회 쪽 문면 하나로 모았다). 받은 연결로
  실행만 한다. commit 하지 않는다.
"""

from datetime import date, timedelta
from typing import Any

from psycopg import sql

from app.core.settings import get_db_schema
from app.finance.schemas.credit_limits import CreditLimitChange


def partner_exists(conn: Any, *, partner_id: str) -> bool:
    """거래처 행이 있는지."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL("SELECT 1 FROM {}.partners WHERE partner_id = %s").format(schema),
            [partner_id],
        )
        return cursor.fetchone() is not None


def select_credit_limit_history(conn: Any, *, partner_id: str, as_of: date) -> list:
    """한 거래처의 여신한도 기간 이력 — 최신 적용일부터, 기준일에 유효한지(`is_current`)와 같이."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL("""
                SELECT partner_credit_limit_id, partner_id, credit_limit_krw,
                       effective_from, effective_to, evidence_grade, source_ref,
                       recorded_by, policy_version, usage_scope, note, is_active,
                       (is_active AND effective_from <= %s
                        AND (effective_to IS NULL OR effective_to >= %s)) AS is_current
                FROM {}.partner_credit_limits
                WHERE partner_id = %s
                ORDER BY effective_from DESC, partner_credit_limit_id DESC
            """).format(schema),
            [as_of, as_of, partner_id],
        )
        return cursor.fetchall()


def lock_active_credit_limits(conn: Any, *, partner_id: str) -> list:
    """한 거래처의 활성 기간을 적용일 순으로 잠그고 읽는다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL("""
                    SELECT partner_credit_limit_id, effective_from, effective_to
                    FROM {}.partner_credit_limits
                    WHERE partner_id = %s AND is_active
                    ORDER BY effective_from
                    FOR UPDATE
                """).format(schema),
            [partner_id],
        )
        return cursor.fetchall()


def close_credit_limit(
    conn: Any, *, partner_credit_limit_id: object, effective_from: date
) -> None:
    """열린 기간을 새 적용일 **전날**로 끝낸다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL("""
                        UPDATE {}.partner_credit_limits
                        SET effective_to = %s, updated_at = now()
                        WHERE partner_credit_limit_id = %s
                    """).format(schema),
            [effective_from - timedelta(days=1), partner_credit_limit_id],
        )


def insert_credit_limit(conn: Any, *, credit_id: str, change: CreditLimitChange) -> None:
    """사용자가 기록한 새 여신한도 기간 한 행 (`manual-v1` · `USER_RECORDED`)."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL("""
                    INSERT INTO {}.partner_credit_limits (
                        partner_credit_limit_id, partner_id, credit_limit_krw, effective_from,
                        evidence_grade, source_ref, recorded_by, policy_version, usage_scope, note
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """).format(schema),
            [
                credit_id,
                change.partner_id,
                change.credit_limit_krw,
                change.effective_from,
                change.evidence_grade,
                change.source_ref,
                change.recorded_by,
                "manual-v1",
                "USER_RECORDED",
                change.note,
            ],
        )
