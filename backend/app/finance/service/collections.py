"""누적 수금 — 사용자 수금 기록(화면 · 마스터 ask)과 하루 수금 단계(마스터가 넘긴 연결).

사용자 수금 기록(`record_collection`)은 한 요청 = 한 트랜잭션이고, 사건 기록
(`master_collection_events`)과 수금 적용이 같은 트랜잭션이다. 하루 수금
(`FinanceCollectionSource`)은 마스터 연결로 적용만 하고 commit 하지 않는다.

전이 계산은 `domain/collections.py`, SQL 은 `repository/collections.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from psycopg import Connection

from app.contracts.parts import CollectionPartOut
from app.core import db as core_db
from app.finance.domain import messages
from app.finance.domain.collections import (
    build_collection_transition,
    collection_event_note,
    collection_target,
    exact_collection_state_id,
    matches_axis,
    require_collection_axis,
)
from app.finance.repository.collections import (
    insert_collection_event,
    lock_collection_state,
    lock_finance_state,
    lock_receivable,
    lock_receivable_amounts,
    update_receivable_collection,
    update_state_collection,
)
from app.finance.schemas.collections import (
    CollectionEvent,
    CollectionTransitionPlan,
    DeterministicCollectionFixtureSource,
    FinanceCollectionConflict,
    ReceivableCollectionChange,
)
from app.finance.schemas.data_port import FinanceDataNotReady
from app.finance.schemas.write_rejection import FinanceWriteRejected


def apply_cumulative_collection(
    conn: Connection[dict[str, object]],
    *,
    receivable_id: str,
    finance_state_id: str,
    target_received_total_krw: object,
) -> CollectionTransitionPlan:
    """잠근 기존 두 행에 누적 수금 delta를 원자적으로 반영한다.

    같은 target을 재적용하면 delta가 0이라 UPDATE도 현금 증가도 없다.
    commit/rollback은 하지 않는다.
    """
    finance_state = lock_finance_state(conn, finance_state_id=finance_state_id)
    if finance_state is None:
        raise LookupError(f"Finance state was not found: {finance_state_id}")
    receivable = lock_receivable(conn, receivable_id=receivable_id)
    if receivable is None:
        raise LookupError(f"Receivable was not found: {receivable_id}")
    plan = build_collection_transition(
        receivable,
        finance_state,
        target_received_total_krw=target_received_total_krw,
    )
    if plan.delta_received_krw == 0:
        return plan
    if update_receivable_collection(conn, plan) != 1:
        raise FinanceCollectionConflict("receivable update did not affect exactly one row")
    if update_state_collection(conn, plan) != 1:
        raise FinanceCollectionConflict("finance state update did not affect exactly one row")
    return plan


def apply_collection_event(
    conn: Connection[dict[str, object]],
    *,
    sim_run_id: str,
    financing_mode: str,
    collection_date: date,
    receivable_id: str,
    target_received_total_krw: object,
) -> CollectionTransitionPlan:
    """명시된 수금 사실을 이미 열린 해당 Finance 일자에만 반영한다.

    이 경계는 ``due_date`` 로 event를 추론하거나 최신 상태를 임의 선택하지 않는다.
    transaction과 실행 축은 호출자가 소유한다.
    """
    require_collection_axis(
        sim_run_id=sim_run_id, financing_mode=financing_mode, receivable_id=receivable_id
    )
    finance_state_id = exact_collection_state_id(
        lock_collection_state(
            conn,
            sim_run_id=sim_run_id,
            financing_mode=financing_mode,
            collection_date=collection_date,
        )
    )
    return apply_cumulative_collection(
        conn,
        receivable_id=receivable_id,
        finance_state_id=finance_state_id,
        target_received_total_krw=target_received_total_krw,
    )


def apply_explicit_collection(
    conn: Connection[dict[str, object]], event: CollectionEvent
) -> CollectionTransitionPlan:
    """명시 fixture event를 누적 수금 전이에 연결한다."""
    return apply_collection_event(
        conn,
        sim_run_id=event.sim_run_id,
        financing_mode=event.financing_mode,
        collection_date=event.collection_date,
        receivable_id=event.receivable_id,
        target_received_total_krw=event.target_received_total_krw,
    )


CollectionEventSource = DeterministicCollectionFixtureSource


@dataclass
class FinanceCollectionSource:
    sim_run_id: str
    financing_mode: str
    source: CollectionEventSource

    def collect(
        self,
        conn: Any,
        *,
        as_of: date,
    ) -> CollectionPartOut:
        events = self.source.events_for_date(
            sim_run_id=self.sim_run_id,
            financing_mode=self.financing_mode,
            as_of=as_of,
        )
        if not events:
            return CollectionPartOut(part="finance", status="NOTHING_DUE")

        collected: list[str] = []
        for event in events:
            if not matches_axis(
                event,
                sim_run_id=self.sim_run_id,
                financing_mode=self.financing_mode,
            ):
                return CollectionPartOut(
                    part="finance",
                    status="BLOCKED",
                    reason="수금 event의 실행 기준이 현재 Finance adapter와 일치하지 않습니다.",
                    collected=collected,
                )
            try:
                plan = apply_explicit_collection(conn, event)
            except (FinanceCollectionConflict, FinanceDataNotReady, LookupError, ValueError) as exc:
                return CollectionPartOut(
                    part="finance",
                    status="BLOCKED",
                    reason=str(exc),
                    collected=collected,
                )
            if plan.delta_received_krw > 0:
                collected.append(plan.receivable_id)
        if not collected:
            return CollectionPartOut(part="finance", status="NOTHING_DUE")
        return CollectionPartOut(part="finance", status="COLLECTED", collected=collected)


def record_collection(
    conn: Connection[dict[str, object]], change: ReceivableCollectionChange
) -> dict[str, object]:
    """사용자가 확인한 한 채권의 실제 전액/부분 수금을 누적 전이로 기록한다.

    화면 `POST /finance/receivables/collections` 와 마스터 ask(FINANCE_COLLECTION_CREATE)가 같은
    함수를 부른다. 순서: 금액 확인 → (한 트랜잭션) 채권 잠금 → 누적 target → 수금 사건 기록 →
    수금 적용. 사건 기록과 적용이 같은 트랜잭션이라 하나가 실패하면 둘 다 물러난다.

    받지 않은 요청: 부분 수금액 없음 INVALID · 채권 없음 NOT_FOUND · 금액 초과 · 같은 날 중복 ·
    전이 불변식 CONFLICT · 그날 재무 상태 없음 CONFLICT(문장은 «해당 기준일의 재무 상태…»).
    """
    if not change.collect_all and change.amount_krw is None:
        raise FinanceWriteRejected("INVALID", messages.PARTIAL_COLLECTION_AMOUNT_REQUIRED)
    try:
        with core_db.transaction(conn):
            row = lock_receivable_amounts(
                conn, receivable_id=change.receivable_id, sim_run_id=change.sim_run_id
            )
            if row is None:
                raise LookupError("받을 돈을 찾지 못했습니다.")
            target = collection_target(
                row, collect_all=change.collect_all, amount_krw=change.amount_krw
            )
            inserted = insert_collection_event(
                conn,
                sim_run_id=change.sim_run_id,
                financing_mode=change.financing_mode,
                collection_date=change.collection_date,
                receivable_id=change.receivable_id,
                target_received_total_krw=target,
                note=collection_event_note(
                    recorded_by=change.recorded_by,
                    source_ref=change.source_ref,
                    note=change.note,
                ),
            )
            if inserted != 1:
                raise ValueError("같은 기준일에 이미 수금이 기록되어 있습니다.")
            plan = apply_collection_event(
                conn,
                sim_run_id=change.sim_run_id,
                financing_mode=change.financing_mode,
                collection_date=change.collection_date,
                receivable_id=change.receivable_id,
                target_received_total_krw=target,
            )
        return {
            "receivable_id": plan.receivable_id,
            "received_delta_krw": plan.delta_received_krw,
            "outstanding_amount_krw": plan.next_outstanding_amount_krw,
            "status": plan.next_status,
        }
    except LookupError as error:
        raise FinanceWriteRejected("NOT_FOUND", str(error)) from error
    except (ValueError, FinanceCollectionConflict) as error:
        raise FinanceWriteRejected("CONFLICT", str(error)) from error
    except FinanceDataNotReady as error:
        raise FinanceWriteRejected("CONFLICT", messages.STATE_NOT_READY_ON_DATE) from error
