"""확정 일정 행 → 현금 사건. 매입채무는 현금이 나가는 날에 얹는다.

SQL 은 `repository/cash_events.py`.
"""

from datetime import date
from decimal import Decimal

from app.finance.domain.tools import effective_cash_date
from app.finance.schemas.agent import CashEvent


def payable_cash_events(rows: list[dict[str, object]], *, as_of: date) -> list[CashEvent]:
    """미결제 매입채무를 현금이 나가는 날에 얹는다.

    제약: 하한을 `due_date > as_of` 로 두면 안 된다. 계약 만기가 토·일인 채무는 실제 현금이
    다음 월요일에 나가는데, 월요일에 실행하면 `due_date < as_of` 가 되어 그 의무가 미래
    현금흐름에서 통째로 사라진다. 원장에 이미 주말 만기 4건이 있다. 연체된 미결제 채무도
    같은 이유로 버리지 않는다.

    대신 상태를 믿는다 — `OPEN` 이면 아직 안 나간 돈이다. 지나간 만기를 임의로 `PAID` 로
    바꾸지 않고, 현금 사건만 `as_of` 이후로 당겨 세운다.

    채권·비용에는 손대지 않는다. 이 하한 완화는 매입채무의 사실이다.
    """
    events: list[CashEvent] = []
    for event in rows_to_events(
        rows,
        id_column="payable_id",
        date_column="due_date",
        amount_column="outstanding_amount_krw",
        event_type="PURCHASE_PAYABLE",
        direction="OUTFLOW",
    ):
        cash_date = max(effective_cash_date(event.event_date), as_of)
        events.append(event.model_copy(update={"event_date": cash_date}))
    return events


def rows_to_events(
    rows: list[dict[str, object]],
    *,
    id_column: str,
    date_column: str,
    amount_column: str,
    event_type: str,
    direction: str,
) -> list[CashEvent]:
    """일정 행을 현금 사건으로 옮긴다. 모양이 다른 행은 받지 않는다."""
    events: list[CashEvent] = []
    for row in rows:
        ref_id = row.get(id_column)
        event_date = row.get(date_column)
        amount = row.get(amount_column)
        if not isinstance(ref_id, str) or not isinstance(event_date, date):
            raise TypeError(f"Invalid scheduled cash event identity: {event_type}")
        if isinstance(amount, bool) or not isinstance(amount, Decimal) or amount < 0:
            raise TypeError(f"Invalid scheduled cash event amount: {ref_id}")
        events.append(
            CashEvent(
                event_date=event_date,
                event_type=event_type,
                amount_krw=amount,
                direction=direction,
                ref_id=ref_id,
            )
        )
    return events
