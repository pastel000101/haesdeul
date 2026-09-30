"""누적 수금 — 수금 사건 · 전이 계획 · 충돌 · 요청.

★ 2026-09-29 재구성 BL-014: 사건 · 계획 · 충돌 · 사건 원천(`DeterministicCollectionFixtureSource`)은
  `finance/collection.py`, 요청 모델(`ReceivableCollectionChange`)은 `finance/router.py` 에서
  옮겼다. 전이 계산은 `domain/collections.py`, 순서 · 트랜잭션은 `service/collections.py`.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

FixtureEvidenceGrade = Literal["SIM_FIXED"]


class FinanceCollectionConflict(ValueError):
    """누적 수금 target 또는 기존 원장 상태가 전이 불변식을 어겼다."""


@dataclass(frozen=True)
class CollectionTransitionPlan:
    receivable_id: str
    finance_state_id: str
    target_received_total_krw: Decimal
    delta_received_krw: Decimal
    next_outstanding_amount_krw: Decimal
    next_status: str
    next_current_cash_krw: Decimal
    next_receivables_krw: Decimal


@dataclass(frozen=True)
class CollectionEvent:
    """호출자가 명시한 매출채권별 누적 수금 사실."""

    sim_run_id: str
    financing_mode: str
    collection_date: date
    receivable_id: str
    target_received_total_krw: object


@dataclass(frozen=True)
class DeterministicCollectionFixtureSource:
    """시뮬레이션 fixture가 명시한 수금 event source."""

    events: tuple[CollectionEvent, ...] = ()
    evidence_grade: FixtureEvidenceGrade = "SIM_FIXED"
    source_ref: str = "finance_collection_fixture"

    @classmethod
    def from_events(
        cls,
        events: Iterable[CollectionEvent],
        *,
        source_ref: str = "finance_collection_fixture",
    ) -> "DeterministicCollectionFixtureSource":
        return cls(events=tuple(events), source_ref=source_ref)

    def events_for_date(
        self,
        *,
        sim_run_id: str,
        financing_mode: str,
        as_of: date,
    ) -> tuple[CollectionEvent, ...]:
        """실행 축과 날짜가 정확히 일치하는 명시 event만 반환한다."""
        return tuple(
            event
            for event in self.events
            if event.sim_run_id == sim_run_id
            and event.financing_mode == financing_mode
            and event.collection_date == as_of
        )


#: 화면 `POST /finance/receivables/collections` 와 마스터 ask(FINANCE_COLLECTION_CREATE)가
#: 같이 쓰는 요청.
class ReceivableCollectionChange(BaseModel):
    """사용자가 확인한 실제 수금. 금액은 이번 수금분이며 누적 target은 서버가 계산한다."""

    sim_run_id: str = Field(min_length=1)
    financing_mode: str = Field(min_length=1)
    collection_date: date
    receivable_id: str = Field(min_length=1)
    collect_all: bool = False
    amount_krw: Decimal | None = Field(default=None, gt=0)
    source_ref: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)
    ]
    recorded_by: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    note: str | None = Field(default=None, max_length=1000)
