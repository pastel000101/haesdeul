"""재무 상태(`finance_states`) 한 행과 축의 모양, 상태 종류(`state_type`) 어휘.

상태 종류 상수는 그 행을 세우는 개장 · 전이 · 취소가 같이 쓴다 — SQL 인자와 판정이 같은 값을
보게 한 곳에 모았다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TypedDict


class FinanceState(TypedDict):
    finance_state_id: str
    sim_run_id: str
    state_date: date
    state_type: str
    financing_mode: str
    current_cash_krw: Decimal
    minimum_operating_cash_krw: Decimal
    committed_outflows_krw: Decimal
    unsettled_purchase_payables_krw: Decimal
    financial_limit_krw: Decimal


class FinanceRuntimeAxis(TypedDict):
    """상태 한 건이 아니라 어느 축 위에서 고르는가."""

    sim_run_id: str
    financing_mode: str


# ``DAY`` is already Finance's generic daily state vocabulary (as opposed to the seeded
# ``DAY0``/``DAY30`` snapshots and the approval-specific ``H1_COMMITMENT`` state).
DAY_OPEN_STATE_TYPE = "DAY"


#: 승인 전이가 만든 상태임을 상태 행에 남긴다. `state_type` 은 재무 소유 컬럼이고
#: 공유 CHECK 도 enum 도 없다 — 다만 `DAY30` 을 그대로 물려주면 승인으로 생긴 행이
#: 번인 마감처럼 읽힌다.
H1_STATE_TYPE = "H1_COMMITMENT"


CANCELLATION_STATE_TYPE = "H1_CANCELLATION"
