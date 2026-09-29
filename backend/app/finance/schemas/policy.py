"""재무 정책을 고르는 좌표 — 정책 버전과 사용 범위.

★ 2026-09-29 재구성 BL-014: `finance/db.py` 에서 옮겼다. 정책 행 SQL 이 이 값을 인자로 쓰고
  (`repository/policy.py`), 정책 검증(`domain/policy_rules.py`)과 DataPort 가 같은 값으로 대조한다.
"""



# ---------------------------------------------------------------------------
# Finance State · Policy · 부채 조회
# ---------------------------------------------------------------------------

FINANCE_POLICY_VERSION = "v1.3-PROVISIONAL"
FINANCE_POLICY_USAGE_SCOPE = "AGENT_MVP_DEMO"
