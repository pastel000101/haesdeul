"""재무 데이터 경계 계약 — `FinanceAsOfDataPort` 와 «준비되지 않음».

경계는 폴더가 아니라 규율이다. DataPort 계약은 구현을 알지 못한다 — 구현은
`readmodel/as_of_data_port.py`(DB 조회)와 `service/agent_run.py`(고정된 런타임 컨텍스트)다.
"""

from datetime import date
from decimal import Decimal
from typing import Protocol

from app.finance.schemas.agent import CashEvent, FinancePolicy
from app.finance.schemas.sales_validation import PartnerReceivable

# ---------------------------------------------------------------------------
# 데이터 경계 계약 — 구현을 import 하지 않는다
# ---------------------------------------------------------------------------

class FinanceDataNotReady(RuntimeError):
    """필수 Finance 사실/Policy가 없거나 과거 시점으로 재현할 수 없다."""

    def __init__(self, key: str):
        # `key` 는 `missing_data` 식별자다 — 기계가 읽으므로 번역하지 않는다.
        # 문장만 사람이 읽는 설명이다 (Controller 경로에서 `reasoning` 이 된다).
        self.key = key
        super().__init__(f"재무 데이터가 준비되지 않았습니다: {key}")


class FinanceAsOfDataPort(Protocol):
    """v2.2 Repository 경계. 모든 변경 가능 읽기에는 ``as_of``를 전달한다."""

    def load_finance_position(self, as_of: date) -> dict[str, object]: ...
    def load_obligations(self, as_of: date, horizon: date) -> list[CashEvent]: ...
    def load_receivables(self, as_of: date, horizon: date) -> list[CashEvent]: ...
    def load_payroll(self, as_of: date, horizon: date) -> Decimal | None: ...
    def load_policy(self, as_of: date, policy_version: str) -> FinancePolicy: ...
    def load_debt_schedule(self, as_of: date, horizon: date) -> list[CashEvent]: ...
    def load_partner_receivables(
        self, as_of: date, partner_id: str
    ) -> list[PartnerReceivable]: ...
    #: 그날 유효한 거래처 여신한도. `None` 은 미확정이고 `0` 은 0원이다.
    def load_partner_credit_limit(
        self, as_of: date, partner_id: str
    ) -> Decimal | None: ...
