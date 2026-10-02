"""매입대금 실제 지급 — 마감이 넘긴 연결로. commit 하지 않는다.

판정은 `domain/settlement.py`, SQL 은 `repository/settlement.py`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.finance.domain.settlement import payable_settlement, state_after_payment
from app.finance.repository.settlement import (
    lock_recognized_payables,
    lock_settlement_state,
    update_payable_settlement,
    update_state_payment,
)
from app.finance.schemas.settlement import (
    FinanceSettlementConflict,
    PayableSettlement,
    SettlementResult,
)


def settle_recognized_payables(
    conn: Any, *, sim_run_id: str, as_of: date
) -> SettlementResult:
    """이 날 현금곡선에 실린 채무를 실제로 지급한다.

    다섯 사실이 한 번에 움직인다. 하나만 움직이면 장부가 서로 다른 말을 한다.

    ```text
    payables.paid_amount_krw          늘어난다
    payables.outstanding_amount_krw   줄어든다
    payables.status                   OPEN/PARTIAL → PARTIAL/SETTLED
    finance_states.current_cash_krw                  줄어든다
    finance_states.unsettled_purchase_payables_krw   줄어든다
    ```

    인식 원장을 고치지 않는다. 어느 채무를 언제 곡선에 실었나는 #615 의 사실이고, 여기서는
    그것을 읽어서 그날 나갈 돈을 정한다.

    미래 채무를 미리 내지 않는다. 인식이 `recognized_date = as_of` 인 것만 본다 — 인식 자체가
    이미 기일과 주말 이월(`effective_cash_date`)을 지나온 결과다.

    멱등: 다시 돌려도 두 번 나가지 않는다. 첫 실행 뒤 `outstanding_amount_krw = 0` 이라 두 번째
    실행은 낼 돈이 0 이고, 그러면 상태도 안 건드린다.
    """
    if not isinstance(sim_run_id, str) or not sim_run_id.strip():
        raise ValueError("sim_run_id must be a non-blank string")

    settled: list[PayableSettlement] = []
    # 인식된 것만, 그리고 아직 낼 돈이 남은 것만. 두 조건이 함께여야 한다 — 인식 없이 내면
    # 곡선에 없는 현금이 나가고, 잔액을 안 보면 두 번 낸다.
    for row in lock_recognized_payables(conn, sim_run_id=sim_run_id, as_of=as_of):
        settlement = payable_settlement(row)
        if settlement is None:
            continue
        if update_payable_settlement(conn, settlement, as_of=as_of) != 1:
            raise FinanceSettlementConflict(
                "payable update did not affect exactly one row"
            )
        settled.append(settlement)

    result = SettlementResult(settled=tuple(settled))
    if result.total_paid_krw > 0:
        _apply_state_payment(
            conn,
            sim_run_id=sim_run_id,
            as_of=as_of,
            paid_krw=result.total_paid_krw,
        )
    return result


def _apply_state_payment(
    conn: Any,
    *,
    sim_run_id: str,
    as_of: date,
    paid_krw: Decimal,
) -> None:
    """나간 돈만큼 그날 재무 상태에서 현금과 미지급 채무를 뺀다.

    상태를 다시 계산하지 않는다. 나간 금액만큼 빼는 것이다 — 다시 세면 그 날 다른 경로가 만든
    값(수금·차입)이 조용히 덮인다.

    `unsettled_purchase_payables_krw` 는 0 아래로 내려가지 않게 막는다. 음수가 되면 그것은
    "채무가 마이너스" 라는 없는 사실이고, 그 값으로 다음 판단이 돈다.
    """
    finance_state_id, next_cash, next_unsettled = state_after_payment(
        lock_settlement_state(conn, sim_run_id=sim_run_id, as_of=as_of), paid_krw=paid_krw
    )
    updated = update_state_payment(
        conn,
        finance_state_id=finance_state_id,
        current_cash_krw=next_cash,
        unsettled_purchase_payables_krw=next_unsettled,
    )
    if updated != 1:
        raise FinanceSettlementConflict(
            "finance state update did not affect exactly one row"
        )
