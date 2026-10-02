"""일반 운영비의 판정 — 새 비용 검사 · 지급 · 취소 가능 여부 · 실제 지급일 읽기.

순서는 `service/expenses.py`, SQL 은 `repository/expenses.py`, 어휘는 `schemas/expenses.py`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.finance.schemas.data_port import FinanceDataNotReady
from app.finance.schemas.expenses import KNOWN_EXPENSE_CATEGORIES, ExpenseConflict

_ZERO = Decimal(0)


def effective_paid_date(
    *, status: str, paid_date: date | None, expense_date: date
) -> date | None:
    """그 비용이 실제로 현금에서 빠진 날로 읽을 날짜.

    ```text
    PAID  + paid_date 있음   → paid_date        (정본)
    PAID  + paid_date 없음   → expense_date     (LEGACY READ COMPATIBILITY ONLY)
    그 외                     → None
    ```

    두 번째 줄은 읽기 전용 호환이다. 마감이 과거 실행을 다시 계산할 때 이미 적힌 지급을 잃지
    않으려고 둔 것이지, `expense_date` 가 지급일이라는 뜻이 아니다. 원장에
    `paid_date = expense_date` 로 적어 넣지 않는다 — 그러면 모르는 것이 아는 것처럼 남고,
    화면은 틀린 날짜를 지급일이라고 말하게 된다.

    그래서 화면 쪽에는 이 함수를 쓰지 않는다. 사용자에게는 «지급일 미상» 이라고 말해야 한다
    (`schemas/console_expenses.py` 의 `paid_date_known`).
    """
    if status != "PAID":
        return None
    return paid_date if paid_date is not None else expense_date


def check_new_expense(
    *,
    sim_run_id: str,
    expense_date: date,
    due_date: date,
    expense_category: str,
    amount_krw: Decimal,
    evidence_id: str,
) -> None:
    """새 비용 한 건을 받을 수 있는지. 받을 수 없으면 `ExpenseConflict`."""
    if not isinstance(sim_run_id, str) or not sim_run_id.strip():
        raise ExpenseConflict("실행 축(sim_run_id)이 필요합니다.")
    if not isinstance(evidence_id, str) or not evidence_id.strip():
        raise ExpenseConflict("비용을 확인할 근거 자료가 필요합니다.")
    if expense_category not in KNOWN_EXPENSE_CATEGORIES:
        # 모르는 분류를 «기타» 로 바꾸지 않는다. 바꾸면 마감이 조용히 다른 칸에 센다.
        raise ExpenseConflict(f"원장이 모르는 비용 분류입니다: {expense_category}")
    if not isinstance(amount_krw, Decimal):
        raise ExpenseConflict("비용 금액은 Decimal 이어야 합니다.")
    if amount_krw <= _ZERO:
        # 0원은 «비용이 없다» 이지 «0원짜리 비용이 있다» 가 아니다.
        raise ExpenseConflict("비용 금액은 0원보다 커야 합니다.")
    if due_date < expense_date:
        raise ExpenseConflict("지급 예정일은 발생일보다 앞설 수 없습니다.")


def payable_expense_amount(rows: list, *, expense_id: str, sim_run_id: str) -> Decimal:
    """잠근 비용 행이 지급할 수 있는 `ACCRUED` 인지 보고 그 금액을 돌려준다."""
    if not rows:
        raise LookupError(f"비용을 찾을 수 없습니다: {expense_id}")
    expense = rows[0]
    if str(expense["sim_run_id"]) != sim_run_id:
        # 남의 실행 비용을 이 실행의 현금에서 빼지 않는다.
        raise ExpenseConflict("다른 실행의 비용은 지급할 수 없습니다.")
    status = str(expense["status"])
    if status == "PAID":
        raise ExpenseConflict("이미 지급된 비용입니다.")
    if status != "ACCRUED":
        raise ExpenseConflict(f"지급할 수 없는 비용 상태입니다: {status}")
    return Decimal(str(expense["amount_krw"]))


def cash_after_payment(current_cash_krw: object, amount: Decimal) -> Decimal:
    """지급 뒤 현금. 0원보다 작아질 수 없다."""
    next_cash = Decimal(str(current_cash_krw)) - amount
    if next_cash < _ZERO:
        raise ExpenseConflict("지급 후 현금이 0원보다 작아질 수 없습니다.")
    return next_cash


def run_financing_mode(rows: list) -> str:
    """이 실행의 조달 축. 부르는 쪽이 넘기지 않고 `sim_runs` 행에서 읽는다.

    없으면 막는다. 실행을 못 찾거나 축이 비어 있으면 기본값을 고르지 않는다 —
    `LOAN_BASELINE` 같은 값을 코드가 정하면, 대출 없는 실행의 현금을 대출 장부에서 빼게 된다.
    어느 장부가 줄었는지 아무도 모르는 편이 더 나쁘다.
    """
    if len(rows) != 1:
        raise FinanceDataNotReady("sim_run")
    financing_mode = rows[0]["financing_mode"]
    if not isinstance(financing_mode, str) or not financing_mode.strip():
        raise FinanceDataNotReady("sim_run_financing_mode")
    return financing_mode.strip()


def check_cancellable_expense(rows: list, *, expense_id: str, sim_run_id: str) -> None:
    """잠근 비용 행이 취소할 수 있는 `ACCRUED` 인지."""
    if not rows:
        raise LookupError(f"비용을 찾을 수 없습니다: {expense_id}")
    expense = rows[0]
    if str(expense["sim_run_id"]) != sim_run_id:
        raise ExpenseConflict("다른 실행의 비용은 취소할 수 없습니다.")
    status = str(expense["status"])
    if status == "PAID":
        raise ExpenseConflict(
            "이미 지급된 비용은 취소할 수 없습니다 — 환입은 별도 정책이 필요합니다."
        )
    if status != "ACCRUED":
        raise ExpenseConflict(f"취소할 수 없는 비용 상태입니다: {status}")
