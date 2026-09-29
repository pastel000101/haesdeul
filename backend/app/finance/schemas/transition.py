"""승인된 매입 약정 → 다음 재무 상태 — 약정 사실(Protocol)과 전이 계획.

★ 2026-09-29 재구성 BL-014: `finance/transition.py` 에서 옮겼다. 계산은 `domain/transition.py`, 읽기
  · 쓰기 순서는
  `service/transition.py`, SQL 은 `repository/transition.py`, 마스터 전이 Protocol 입구는
  `adapter.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol


class ArrivalLegFacts(Protocol):
    """회차에서 재무가 읽는 칸.

    ★ `payment_due_date` 는 **선택 칸**이다 (`_due_date_of` 가 `getattr` 로 읽는다). 마스터
      약정에 있으면 그 값이 채무 만기일이고, 없으면 매입일 + 정책 N5 로 계산한다.
    """

    seq: int
    purchase_date: date
    amount_krw: float | None


class ApprovedCommitmentFacts(Protocol):
    """승인 약정에서 **재무가 읽는 것만**.

    ★ 정본은 마스터의 `ApprovedCommitment` 다. 재무는 그 모듈을 import 하지 않는다 —
      재무가 닿아도 되는 마스터 표면은 공유 계약(`envelope` · `critic_bridge`)뿐이고,
      그 선은 `test_finance_sales_orchestration_boundary` 가 지킨다. 여기 적힌 것은
      복제한 모델이 아니라 **의존하는 필드 목록**이다.
    """

    approval_id: str
    as_of: date
    total_amount_krw: float
    arrival_schedule: Sequence[ArrivalLegFacts]


@dataclass(frozen=True)
class FinancePayableWrite:
    """승인이 만드는 매입채무 한 건. **금액도 날짜도 지어내지 않는다.**"""

    payable_id: str
    sim_run_id: str
    purchase_id: str
    issued_date: date
    #: **계약 만기일**이다. 주말이어도 그대로 원장에 남는다 — 현금이 실제로 나가는
    #: 날은 `tools.effective_cash_date` 가 조회 시점에 정한다.
    due_date: date
    amount_krw: Decimal


@dataclass(frozen=True)
class FinanceTransitionPlan:
    """승인 1건이 만드는 재무 변경 전부. **아직 아무것도 쓰지 않았다.**"""

    approval_id: str
    sim_run_id: str
    #: 이 계산이 딛고 선 T0 행. 나머지 컬럼은 여기서 그대로 이어 간다.
    source_finance_state_id: str
    next_finance_state_id: str
    #: 다음 상태가 서는 날. **부르는 쪽이 준 값**이다 — 재무가 세지 않는다.
    #: 달력일이라 토·일도 그대로 선다.
    next_state_date: date
    payables: tuple[FinancePayableWrite, ...]
    #: 승인 뒤 미결제 매입채무 총액. 현금은 **바뀌지 않는다.**
    next_unsettled_purchase_payables_krw: Decimal

    @property
    def payable_total_krw(self) -> Decimal:
        return sum((row.amount_krw for row in self.payables), Decimal(0))
