"""일반 운영비 — 화면(`/finance/expenses*`)과 마스터 ask 의 비용 쓰기, 걷기의 일괄 지급.

트랜잭션 경계가 둘이다. 화면 · ask 의 쓰기(`accrue_operating_expense` · `pay_accrued_expense` ·
`cancel_accrued_expense`)는 받은 연결에 한 요청 = 한 트랜잭션을 연다. 걷기의 일괄 지급
(`settle_due_expenses`)은 마스터가 넘긴 연결로 돌고 commit 하지 않는다. 한 건 지급의 순서
(`settle_expense`)는 두 경로가 같이 쓴다.

판정은 `domain/expenses.py`, SQL 은 `repository/expenses.py`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.core import db as core_db
from app.finance.domain import messages
from app.finance.domain.expenses import (
    cash_after_payment,
    check_cancellable_expense,
    check_new_expense,
    payable_expense_amount,
    run_financing_mode,
)
from app.finance.domain.finance_state import exact_state_row
from app.finance.repository.expenses import (
    insert_expense,
    lock_expense_for_cancel,
    lock_expense_for_settlement,
    mark_expense_cancelled,
    mark_expense_paid,
    select_due_expense_ids,
    select_run_financing_mode,
)
from app.finance.repository.finance_states import lock_state_on_date, update_state_cash
from app.finance.schemas.data_port import FinanceDataNotReady
from app.finance.schemas.expenses import (
    ExpenseCancel,
    ExpenseConflict,
    ExpenseCreate,
    ExpenseSettle,
    ExpenseSettlement,
)
from app.finance.schemas.write_rejection import FinanceWriteRejected


def create_expense(
    conn: Any,
    *,
    sim_run_id: str,
    expense_date: date,
    due_date: date,
    expense_category: str,
    amount_krw: Decimal,
    evidence_id: str,
    related_delivery_id: str | None = None,
    is_fixed: bool = False,
    note: str | None = None,
) -> str:
    """발생한 비용 하나를 `ACCRUED` 로 적는다. 현금은 건드리지 않는다.

    현금은 지급할 때 빠진다. 발생 시점에 빼면 아직 나가지 않은 돈이 없는 것으로 적히고, 그 뒤
    지급하면 같은 돈이 두 번 빠진다.

    `due_date` 는 필수다. 지급 예정일이 없으면 미래 투영이 이 의무를 놓친다.

    commit 은 부르는 쪽이 한다.
    """
    check_new_expense(
        sim_run_id=sim_run_id,
        expense_date=expense_date,
        due_date=due_date,
        expense_category=expense_category,
        amount_krw=amount_krw,
        evidence_id=evidence_id,
    )
    expense_id = f"EXP-{uuid4()}"
    inserted = insert_expense(
        conn,
        expense_id=expense_id,
        sim_run_id=sim_run_id,
        expense_date=expense_date,
        due_date=due_date,
        expense_category=expense_category,
        amount_krw=amount_krw,
        is_fixed=is_fixed,
        related_delivery_id=related_delivery_id,
        evidence_id=evidence_id,
        note=note,
    )
    if inserted != 1:
        raise ExpenseConflict("비용을 원장에 적지 못했습니다.")
    return expense_id


def settle_expense(
    conn: Any,
    *,
    expense_id: str,
    sim_run_id: str,
    financing_mode: str,
    paid_date: date,
) -> ExpenseSettlement:
    """`ACCRUED` 비용 하나를 지급하고 같은 거래에서 현금을 줄인다.

    ```text
    비용 행 잠금 → 상태·실행 축 확인 → 재무 상태 행 잠금 → 현금 차감 → PAID 기록
    ```

    멱등: 두 번 눌러도 한 번만 빠진다. 같은 요청이 재시도되면 두 번째는 상태가 이미 `PAID`
    라 잠금 뒤에서 막힌다 — 별도 멱등 표를 만들지 않아도 상태 자체가 잠금 역할을 한다. 상태를
    읽고 나서 잠그면 두 요청이 같은 `ACCRUED` 를 함께 보고 둘 다 통과하므로, 잠그고 나서
    읽는 순서를 바꾸지 않는다.

    재무 상태는 정확히 한 행이어야 한다. 실행·장부·기준일 셋으로 고른다. 못 찾으면 막고, 둘
    이상이면 막는다 — «최신 상태» 로 대신 고르면 다른 실행이나 다른 장부의 현금이 줄어든다.

    commit 은 부르는 쪽이 한다 (화면·ask 는 한 요청 한 트랜잭션, 걷기는 마스터 연결).
    """
    amount = payable_expense_amount(
        lock_expense_for_settlement(conn, expense_id=expense_id),
        expense_id=expense_id,
        sim_run_id=sim_run_id,
    )
    state = exact_state_row(
        lock_state_on_date(
            conn, sim_run_id=sim_run_id, financing_mode=financing_mode, state_date=paid_date
        )
    )
    next_cash = cash_after_payment(state["current_cash_krw"], amount)
    if mark_expense_paid(conn, expense_id=expense_id, paid_date=paid_date) != 1:
        raise ExpenseConflict("비용 상태를 지급으로 바꾸지 못했습니다.")
    updated = update_state_cash(
        conn, finance_state_id=state["finance_state_id"], current_cash=next_cash
    )
    if updated != 1:
        raise ExpenseConflict("재무 상태를 갱신하지 못했습니다.")
    return ExpenseSettlement(
        expense_id=expense_id,
        paid_date=paid_date,
        amount_krw=amount,
        current_cash_krw=next_cash,
    )


def settle_due_expenses(
    conn: Any,
    *,
    sim_run_id: str,
    as_of: date,
) -> tuple[ExpenseSettlement, ...]:
    """`as_of` 까지 지급일이 된 `ACCRUED` 비용을 기존 `settle_expense()` 로 지급한다.

    지급할 비용을 `due_date, expense_id` 순으로 골라 한 건씩 `settle_expense()` 에 넘긴다.
    계약은 아래 여섯 줄이다.

    ```text
    ①  conn 은 부르는 쪽이 소유한다            `settle_expense()` 와 같은 규율
                                             이 함수는 commit 하지 않는다
    ②  반환은 기존 `ExpenseSettlement`         새 결과형을 만들지 않는다
    ③  지급할 것이 없으면 빈 튜플               예외가 아니다
    ④  실패는 올린다                           `ExpenseConflict` · `FinanceDataNotReady`
                                             삼키지 않는다 — 부르는 쪽이 fail-closed 로 받는다
    ⑤  순서 `ORDER BY due_date, expense_id`    결정론
    ⑥  `financing_mode` 는 이 함수가 `sim_run` 에서 읽는다   부르는 쪽이 넘기지 않는다
    ```

    ④ 가 가장 중요하다. 지급이 실패했는데 그날이 정상 `CLOSED` 로 서면 현금은 줄었는데
    비용은 0원인 기록이 남는다. 그래서 여기서 삼키지 않고, 부르는 쪽
    (`master/service/scheduler.py`)이 그날 마감을 막는다.

    한 트랜잭션이다. 여러 건을 지급하다 중간에서 터지면 앞선 지급까지 같이 되돌아가야 한다 —
    그래서 ① 로 커넥션을 부르는 쪽에 둔다. 절반만 나간 상태로 커밋되면 현금과 원장이 갈린다.

    :param conn: 부르는 쪽이 연 커넥션. 이 함수는 commit 하지 않는다.
    :param sim_run_id: 실행 축. 다른 실행의 비용을 지급하지 않는다.
    :param as_of: 이 날짜까지 지급일이 된 것을 지급하고, `paid_date` 로 적는다.
    :returns: 지급한 건들. 없으면 빈 튜플.
    """
    # 축을 먼저 확인한다. 지급할 것이 없어도 실행 축은 실재해야 한다. 순서를 뒤집어 «대상
    # 0건이면 그냥 빈 튜플» 로 끝내면, 없는 실행을 물어도 «지급할 것이 없었다» 로 답한다 —
    # 축이 틀렸다는 사실이 조용히 사라진다.
    #
    # 같은 커넥션으로 읽는다. 지급과 축 조회가 다른 거래에서 일어나면, 되돌아간 지급이 읽은
    # 축과 달라질 수 있다.
    financing_mode = run_financing_mode(select_run_financing_mode(conn, sim_run_id=sim_run_id))
    due = select_due_expense_ids(conn, sim_run_id=sim_run_id, as_of=as_of)

    # 지급 로직을 다시 쓰지 않는다. 상태 잠금 · 중복 지급 방어 · 현금 차감 · 부족 검사는
    # 모두 `settle_expense()` 가 주인이다. 여기서 한 벌 더 만들면 둘이 언젠가 어긋나고, 그때
    # 어느 쪽이 정본인지 아무도 말할 수 없다.
    #
    # `paid_date` 는 실제로 지급한 날(`as_of`)이다. 지급일(`due_date`)을 그대로 쓰면 늦게
    # 나간 돈이 제 날짜에 나간 것처럼 적힌다.
    settled: list[ExpenseSettlement] = []
    for expense_id in due:
        settled.append(
            settle_expense(
                conn,
                expense_id=expense_id,
                sim_run_id=sim_run_id,
                financing_mode=financing_mode,
                paid_date=as_of,
            )
        )
    return tuple(settled)


def cancel_expense(conn: Any, *, expense_id: str, sim_run_id: str) -> str:
    """`ACCRUED` 비용을 «나가지 않기로 한다» 로 적는다. 현금은 변하지 않는다.

    이미 지급된 비용은 여기로 못 온다. 돈이 나간 사실은 취소로 지워지지 않는다.
    commit 은 부르는 쪽이 한다.
    """
    check_cancellable_expense(
        lock_expense_for_cancel(conn, expense_id=expense_id),
        expense_id=expense_id,
        sim_run_id=sim_run_id,
    )
    if mark_expense_cancelled(conn, expense_id=expense_id) != 1:
        raise ExpenseConflict("비용 상태를 취소로 바꾸지 못했습니다.")
    return expense_id


# ---------------------------------------------------------------------------
# 화면 · 마스터 ask 의 비용 쓰기 — 한 요청 = 한 트랜잭션
#
# 라우터와 마스터 ask 가 이 함수들을 부른다. 이름은 라우터 핸들러(OpenAPI 이름)와 겹치지 않게
# 지었다. 걷기의 일괄 지급(`settle_due_expenses`)은 이 경계를 쓰지 않는다 — 마스터 연결에서
# 돈다.
# ---------------------------------------------------------------------------


def accrue_operating_expense(conn: core_db.Connection, expense: ExpenseCreate) -> dict[str, object]:
    """운영비 한 건을 `ACCRUED` 로 적는다. 받지 않은 요청: 비용 충돌 CONFLICT."""
    try:
        with core_db.transaction(conn):
            expense_id = create_expense(conn, **expense.model_dump())
    except ExpenseConflict as error:
        raise FinanceWriteRejected("CONFLICT", str(error)) from error
    return {"expense_id": expense_id, "status": "ACCRUED"}


def pay_accrued_expense(
    conn: core_db.Connection, expense_id: str, request: ExpenseSettle
) -> dict[str, object]:
    """`ACCRUED` 비용을 지급하고 같은 거래에서 현금을 줄인다.

    받지 않은 요청: 비용 없음 NOT_FOUND · 비용 충돌 CONFLICT · 지급일 재무 상태 없음 CONFLICT
    (문장은 «해당 지급일의 재무 상태…»).
    """
    try:
        with core_db.transaction(conn):
            result = settle_expense(conn, expense_id=expense_id, **request.model_dump())
    except LookupError as error:
        raise FinanceWriteRejected("NOT_FOUND", str(error)) from error
    except ExpenseConflict as error:
        raise FinanceWriteRejected("CONFLICT", str(error)) from error
    except FinanceDataNotReady as error:
        raise FinanceWriteRejected("CONFLICT", messages.STATE_NOT_READY_ON_PAID_DATE) from error
    return {
        "expense_id": result.expense_id,
        "status": "PAID",
        "paid_date": result.paid_date,
        "amount_krw": result.amount_krw,
        "current_cash_krw": result.current_cash_krw,
    }


def cancel_accrued_expense(
    conn: core_db.Connection, expense_id: str, request: ExpenseCancel
) -> dict[str, object]:
    """`ACCRUED` 비용을 취소한다. 받지 않은 요청: 비용 없음 NOT_FOUND · 비용 충돌 CONFLICT."""
    try:
        with core_db.transaction(conn):
            cancel_expense(conn, expense_id=expense_id, **request.model_dump())
    except LookupError as error:
        raise FinanceWriteRejected("NOT_FOUND", str(error)) from error
    except ExpenseConflict as error:
        raise FinanceWriteRejected("CONFLICT", str(error)) from error
    return {"expense_id": expense_id, "status": "CANCELLED"}
