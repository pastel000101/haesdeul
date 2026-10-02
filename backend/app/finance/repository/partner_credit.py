"""거래처 채권 · 여신한도 SQL (판정 경로)."""

from datetime import date
from typing import Any

from psycopg import sql

from app.core.settings import get_db_schema
from app.finance.repository._cursor import fetch_all

# ---------------------------------------------------------------------------
# 거래처 매출채권 — 실 원장에서 읽는다
#
# 이 절이 소유하는 것은 조회와 옮겨 담기뿐이다. 무엇이 미회수인지, 무엇이 연체인지는
# `domain/tools.py` 의 `summarize_partner_receivables` 가 소유한다 — 어휘를 SQL 에도 한 번
# 더 적으면 둘이 조용히 갈라진다.
#
# 못 읽은 것을 "채권 없음" 으로 바꾸지 않는다. 연결 실패 · 조회 실패 · 모양이 다른 값은 전부
# `FinanceDataNotReady` 로 세운다. 0원 채권은 조회가 성공했고 미회수 행이 0건일 때만 나오는
# 사실이다.
# ---------------------------------------------------------------------------


def select_partner_receivables(
    conn: Any, *, sim_run_id: str, as_of: date, partner_id: str
) -> list[dict[str, object]]:
    """거래처 채권 행. 같은 sim_run 안에서만, as_of 까지만 본다.

    `sim_run_id` 를 채권과 판매 양쪽에 모두 건다. 조인 한쪽만 걸면 다른 실행의 판매 Header 를
    타고 남의 run 채권이 딸려 들어온다.

    `issued_date <= as_of` 와 `sale_date <= as_of` 를 함께 건다. 발행일만 막으면 아직 일어나지
    않은 판매에 붙은 채권이 과거 시점 조회에 섞인다 — as-of 재현이 깨지는 순간이다.
    """
    schema = get_db_schema()
    query = sql.SQL(
        """
        SELECT
            r.receivable_id,
            r.due_date,
            r.outstanding_amount_krw,
            r.status
        FROM {}.{} AS r
        JOIN {}.{} AS s
          ON s.sale_id = r.sale_id
         AND s.sim_run_id = r.sim_run_id
        WHERE r.sim_run_id = %s
          AND s.sim_run_id = %s
          AND s.customer_partner_id = %s
          AND r.issued_date <= %s
          AND s.sale_date <= %s
        ORDER BY r.receivable_id
        """
    ).format(
        sql.Identifier(schema),
        sql.Identifier("receivables"),
        sql.Identifier(schema),
        sql.Identifier("sales"),
    )
    return fetch_all(conn, query, [sim_run_id, sim_run_id, partner_id, as_of, as_of])


def select_active_credit_limits(
    conn: Any, *, partner_id: str, as_of: date
) -> list[dict[str, object]]:
    """그날 유효한 활성 여신한도 구간 (최대 두 행 — 겹침을 알아보려고)."""
    query = sql.SQL(
        """
        SELECT partner_credit_limit_id, credit_limit_krw
        FROM {}.partner_credit_limits
        WHERE partner_id = %s
          AND is_active
          AND effective_from <= %s
          AND (effective_to IS NULL OR effective_to >= %s)
        ORDER BY effective_from DESC
        LIMIT 2
        """
    ).format(sql.Identifier(get_db_schema()))
    return fetch_all(conn, query, [partner_id, as_of, as_of])
