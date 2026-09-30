"""재무 active policy 행 SQL.

★ 2026-09-29 재구성 BL-014: `finance/db.py` 에서 옮겼다.
"""

from typing import Any

from psycopg import sql

from app.core.settings import get_db_schema
from app.finance.repository._cursor import fetch_all
from app.finance.schemas.policy import FINANCE_POLICY_USAGE_SCOPE, FINANCE_POLICY_VERSION


def select_policy_rows(conn: Any) -> list[dict[str, object]]:
    """재무 active policy 행 (정책 값 칸)."""
    query = sql.SQL(
        """
        SELECT
            policy_key,
            value_kind,
            value_numeric,
            value_text,
            value_json,
            source_ref,
            policy_version,
            usage_scope
        FROM {}.agent_policy_config
        WHERE domain = %s
          AND policy_version = %s
          AND usage_scope = %s
          AND is_active = TRUE
        """
    ).format(sql.Identifier(get_db_schema()))
    return fetch_all(
        conn,
        query,
        ["finance", FINANCE_POLICY_VERSION, FINANCE_POLICY_USAGE_SCOPE],
    )


def select_debt_policy_rows(conn: Any) -> list[dict[str, object]]:
    """재무 active policy 행 (부채 정책은 근거 등급 칸까지)."""
    query = sql.SQL(
        """
        SELECT
            policy_key,
            value_kind,
            value_numeric,
            value_text,
            value_json,
            evidence_grade,
            source_ref,
            policy_version,
            usage_scope
        FROM {}.agent_policy_config
        WHERE domain = %s
          AND policy_version = %s
          AND usage_scope = %s
          AND is_active = TRUE
        """
    ).format(sql.Identifier(get_db_schema()))
    return fetch_all(
        conn,
        query,
        ["finance", FINANCE_POLICY_VERSION, FINANCE_POLICY_USAGE_SCOPE],
    )
