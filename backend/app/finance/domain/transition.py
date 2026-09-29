"""승인 약정 → 재무 변경 계산 — 회차 금액 · 지급기일 · 매입 ID · 다음 상태.

★ 2026-09-29 재구성 BL-014: `finance/transition.py` 를 판정 · 순서 · SQL 로 나눴다. 읽기는
  `service/transition.py`
  가 먼저 하고 계산은 입출력 없이 한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from app.finance.domain.state_identity import daily_finance_state_id
from app.finance.schemas.data_port import FinanceDataNotReady
from app.finance.schemas.transition import (
    ApprovedCommitmentFacts,
    FinancePayableWrite,
    FinanceTransitionPlan,
)


def transition_plan(
    commitment: ApprovedCommitmentFacts,
    *,
    state: Mapping[str, object],
    purchase_payment_days: int | None,
    target_state_date: date,
    purchase_ids: Mapping[int, str],
) -> FinanceTransitionPlan:
    """승인일 상태 · 정책 N5 · 약정으로 재무 변경을 계산한다. **입출력 없음.**"""
    if purchase_payment_days is None:
        raise FinanceDataNotReady("purchase_payment_days")

    amount = Decimal(str(commitment.total_amount_krw))
    if amount <= 0:
        raise FinanceDataNotReady("commitment_total_amount")

    sim_run_id = str(state["sim_run_id"])
    financing_mode = str(state["financing_mode"])
    legs = _payment_legs(commitment, total_amount=amount)
    payables = tuple(
        FinancePayableWrite(
            # 회차는 매입 의무의 축이다. 배열 위치가 아니라 Master가 실어 준 seq를 쓴다.
            payable_id=f"AP-{commitment.approval_id}-S{leg.seq}",
            sim_run_id=sim_run_id,
            purchase_id=_purchase_id_for_leg(purchase_ids, leg.seq),
            issued_date=commitment.as_of,
            # N5 는 계약 지급일까지의 달력일수다 (현재 0 = 매입 당일). 주말 보정은
            # 하지 않는다. 실제 현금일만 `tools.effective_cash_date`가 옮긴다.
            due_date=_due_date_of(leg, payment_days=int(purchase_payment_days)),
            amount_krw=leg.amount_krw,
        )
        for leg in legs
    )
    return FinanceTransitionPlan(
        approval_id=commitment.approval_id,
        sim_run_id=sim_run_id,
        source_finance_state_id=str(state["finance_state_id"]),
        next_finance_state_id=daily_finance_state_id(
            sim_run_id=sim_run_id,
            financing_mode=financing_mode,
            state_date=target_state_date,
        ),
        next_state_date=target_state_date,
        payables=payables,
        next_unsettled_purchase_payables_krw=(
            Decimal(str(state["unsettled_purchase_payables_krw"])) + amount
        ),
    )


@dataclass(frozen=True)
class _PaymentLeg:
    seq: int
    purchase_date: date
    amount_krw: Decimal
    #: 마스터 약정 회차가 실어 준 지급기일. 없으면 `None` — 정책 N5 로 계산한다.
    payment_due_date: date | None = None


def _due_date_of(leg: _PaymentLeg, *, payment_days: int) -> date:
    """채무 만기일. **약정 회차에 확정 지급기일이 있으면 그 값이다** (재무 요청 2026-09-16).

    ★ 정책 주석(`finance_policy_seed.sql` purchase_payment_days)이 *"H1에 확정 payment_date가
      존재하면 해당 값이 authoritative"* 라고 적어 두었다. 실매입 기록의 지급기일과 채무
      `due_date` 가 한 값이어야 한다. 없을 때만 매입일 + N5 달력일로 계산한다.
    """
    if leg.payment_due_date is not None:
        return leg.payment_due_date
    return leg.purchase_date + timedelta(days=payment_days)


def _payment_legs(
    commitment: ApprovedCommitmentFacts, *, total_amount: Decimal
) -> tuple[_PaymentLeg, ...]:
    """Master가 확정한 회차 금액을 원화 Decimal 채무로 좁힌다.

    단일 회차의 금액이 아직 없는 오래된 입력은 약정 총액과 축이 하나뿐이므로 기존
    의미를 보존한다. 회차가 둘 이상이면 모든 `amount_krw`가 정본으로 와야 한다.
    일부/전부 누락, 음수·비유한 값, 총액 불일치를 재무가 비율 배분이나 0으로 고치지
    않는다.
    """
    legs = tuple(commitment.arrival_schedule)
    if not legs:
        raise FinanceDataNotReady("commitment_arrival_schedule")

    raw_amounts = tuple(getattr(leg, "amount_krw", None) for leg in legs)
    if len(legs) > 1 and any(value is None for value in raw_amounts):
        raise FinanceDataNotReady("commitment_payment_amounts")

    resolved: list[_PaymentLeg] = []
    for leg, raw_amount in zip(legs, raw_amounts, strict=True):
        if raw_amount is None:
            leg_amount = total_amount
        else:
            try:
                leg_amount = Decimal(str(raw_amount))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise FinanceDataNotReady("commitment_payment_amounts") from exc
        # Master의 현재 금액 계약은 0과 missing을 구분한다. 0은 유효하지만 음수와
        # NaN/Infinity는 원장 금액으로 쓸 수 없다.
        if not leg_amount.is_finite() or leg_amount < 0:
            raise FinanceDataNotReady("commitment_payment_amounts")
        due = getattr(leg, "payment_due_date", None)
        if due is not None and not isinstance(due, date):
            raise FinanceDataNotReady("commitment_payment_due_date")
        resolved.append(_PaymentLeg(leg.seq, leg.purchase_date, leg_amount, due))

    if sum((leg.amount_krw for leg in resolved), Decimal(0)) != total_amount:
        raise FinanceDataNotReady("commitment_payment_amounts")
    return tuple(resolved)


def _purchase_id_for_leg(purchase_ids: Mapping[int, str], seq: int) -> str:
    """이 회차의 매입 ID. **없으면 다른 값으로 대신하지 않는다.**

    🔴 매핑에 값이 하나뿐이라고 그것을 집으면, 마스터가 다른 회차 ID 를 실어 준
       날에 **엉뚱한 매입에 채무가 붙는다.** 에러 없이 원장만 어긋난다.
    """
    if seq not in purchase_ids:
        raise FinanceDataNotReady("commitment_purchase_ids")
    purchase_id = purchase_ids[seq]
    if not isinstance(purchase_id, str) or not purchase_id.strip():
        raise FinanceDataNotReady("commitment_purchase_ids")
    return purchase_id
