"""재무 active policy 조회.

★ 2026-09-29 재구성 BL-014: `finance/db.py` 에서 옮겼다.
"""

from app.core import db as core_db
from app.finance.domain.policy_rules import build_finance_debt_policy, build_finance_policy
from app.finance.repository.policy import select_debt_policy_rows, select_policy_rows
from app.finance.schemas.agent import FinanceDebtPolicy, FinancePolicy


def get_active_finance_policy() -> FinancePolicy:
    """현재 Finance MVP 범위의 active policy를 typed contract로 조회한다."""
    with core_db.read_connection() as conn:
        return build_finance_policy(select_policy_rows(conn))


def get_active_finance_debt_policy() -> FinanceDebtPolicy:
    """현재 Finance MVP 범위의 SIM_FIXED debt contract를 조회한다."""
    with core_db.read_connection() as conn:
        return build_finance_debt_policy(select_debt_policy_rows(conn))
