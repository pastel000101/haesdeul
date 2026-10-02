"""누적 수금 전이 계산 — 누적 target 을 delta 로. DB 를 바꾸지 않는다.

순서 · 트랜잭션은 `service/collections.py`.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from app.finance.domain.values import row_value
from app.finance.schemas.collections import (
    CollectionEvent,
    CollectionTransitionPlan,
    FinanceCollectionConflict,
)
from app.finance.schemas.data_port import FinanceDataNotReady


def build_collection_transition(
    receivable: Mapping[str, object],
    finance_state: Mapping[str, object],
    *,
    target_received_total_krw: object,
) -> CollectionTransitionPlan:
    """누적 target을 delta 전이로 계산한다. DB를 변경하지 않는다."""
    target = _money(target_received_total_krw, "target_received_total_krw")
    original = _money(receivable.get("original_amount_krw"), "original_amount_krw")
    current = _money(receivable.get("received_amount_krw"), "received_amount_krw")
    outstanding = _money(receivable.get("outstanding_amount_krw"), "outstanding_amount_krw")
    if original - current != outstanding:
        raise FinanceCollectionConflict("receivable amount identity is inconsistent")
    current_status = str(receivable.get("status"))
    if current_status not in {"OPEN", "PARTIAL", "COLLECTED"}:
        raise FinanceCollectionConflict("receivable status cannot accept collection")
    if current_status == "COLLECTED" and current != original:
        raise FinanceCollectionConflict("collected receivable is not fully received")
    if target < current:
        raise FinanceCollectionConflict("cumulative collection cannot regress")
    if target > original:
        raise FinanceCollectionConflict("cumulative collection cannot exceed original amount")

    delta = target - current
    next_outstanding = original - target
    current_cash = _money(finance_state.get("current_cash_krw"), "current_cash_krw")
    receivables = _money(finance_state.get("receivables_krw"), "receivables_krw")
    if receivables < delta:
        raise FinanceCollectionConflict("finance state receivables cannot cover collection delta")
    if receivable.get("sim_run_id") != finance_state.get("sim_run_id"):
        raise FinanceCollectionConflict("receivable and finance state axes do not match")
    status = "COLLECTED" if target == original else "PARTIAL" if target > 0 else "OPEN"
    return CollectionTransitionPlan(
        receivable_id=str(receivable["receivable_id"]),
        finance_state_id=str(finance_state["finance_state_id"]),
        target_received_total_krw=target,
        delta_received_krw=delta,
        next_outstanding_amount_krw=next_outstanding,
        next_status=status,
        next_current_cash_krw=current_cash + delta,
        next_receivables_krw=receivables - delta,
    )


def require_collection_axis(*, sim_run_id: str, financing_mode: str, receivable_id: str) -> None:
    """수금 사실의 축 세 칸은 비어 있을 수 없다."""
    for field, value in (
        ("sim_run_id", sim_run_id),
        ("financing_mode", financing_mode),
        ("receivable_id", receivable_id),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-blank string")


def exact_collection_state_id(rows: list) -> str:
    """수금일의 재무 상태는 정확히 한 행이어야 한다."""
    if not rows:
        raise FinanceDataNotReady("historical_finance_position")
    if len(rows) != 1:
        raise FinanceDataNotReady("finance_state_ambiguous")
    return str(row_value(rows[0], "finance_state_id", 0))


def _money(value: object, field: str) -> Decimal:
    if isinstance(value, (bool, float)) or value is None:
        raise FinanceCollectionConflict(f"{field} is not an exact monetary value")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise FinanceCollectionConflict(f"{field} is not an exact monetary value") from exc
    if not amount.is_finite() or amount < 0:
        raise FinanceCollectionConflict(f"{field} must be a non-negative finite amount")
    return amount


def matches_axis(event: CollectionEvent, *, sim_run_id: str, financing_mode: str) -> bool:
    return event.sim_run_id == sim_run_id and event.financing_mode == financing_mode


def collection_target(
    row: object, *, collect_all: bool, amount_krw: Decimal | None
) -> Decimal:
    """이번 사용자 수금 뒤의 누적 수금액. 남은 받을 돈을 넘으면 받지 않는다.

    금액은 이번 수금분이고 누적 target 은 여기서 계산한다 — 전액이면 원금, 아니면 지금까지
    받은 돈 + 이번 수금분.
    """
    original = Decimal(str(row["original_amount_krw"]))  # type: ignore[index]
    received = Decimal(str(row["received_amount_krw"]))  # type: ignore[index]
    target = original if collect_all else received + amount_krw  # type: ignore[operator]
    if target > original:
        raise ValueError("받는 금액이 남은 받을 돈보다 큽니다.")
    return target


def collection_event_note(*, recorded_by: str, source_ref: str, note: str | None) -> str:
    """사용자 수금 사건에 남길 메모 — 입력자와 근거를 붙인다."""
    event_note = f"사용자 수금 · 입력자 {recorded_by} · 근거 {source_ref}"
    if note:
        event_note = f"{event_note} · {note}"
    return event_note
