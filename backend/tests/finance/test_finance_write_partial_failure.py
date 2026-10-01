"""재무 화면 쓰기 6종 — **일부를 쓴 뒤** 터지면 그 요청이 쓴 것을 전부 되돌리는가.

★ 2026-10-01 재구성 BL-024: 쓰기 6종의 트랜잭션 블록은 재무 service 가 받은 연결에 연다
  (`core_db.transaction(conn)` — 정상 종료 commit · 예외 rollback). 지금까지의 실패 검사는 모두
  **쓰기 전에** 받지 않은 요청(없는 거래처 · 겹치는 기간 · 없는 재무 상태)이었다. 여기서는 앞
  문장이 실제로 실행된 뒤 뒤 문장이 터지는 자리를 잰다.

재는 것 (요청마다):

1. 터지기 **전** 문장이 실행됐다 — 주입한 자리에 실제로 닿았다.
2. 그 연결에 `rollback` 한 번, `commit` 없음.
3. 업무 거절(`FinanceWriteRejected`)로 바꾸지 않고 **원래 예외를 그대로** 올린다.

★ DB 를 치지 않는다. 가짜 연결이 SQL 문면을 보고 행을 돌려주고, 정한 문장에서 예외를 낸다.
  SQL 은 진짜 repository 가 짓는다. PostgreSQL 이 실제로 행을 되돌리는지는 이 파일이 재지 않는다 —
  격리 DB 실행 기록은 DB 작업 폴더 `verify/bl024_failure_injection` 에 있다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.finance.schemas.cash_adjustments import CashAdjustmentChange
from app.finance.schemas.collections import ReceivableCollectionChange
from app.finance.schemas.credit_limits import CreditLimitChange
from app.finance.schemas.expenses import ExpenseCancel, ExpenseCreate, ExpenseSettle
from app.finance.service import cash_adjustments, collections, credit_limits, expenses
from tests.finance.finance_fake_connection import FakeConnection

pytestmark = pytest.mark.usefixtures("db_schema_env")

RUN = "SIM-WRITE-FAIL"
MODE = "LOAN_BASELINE"
DAY = date(2026, 9, 18)
STATE_ID = "FIN-DAY-1"
EXPENSE_ID = "EXP-1"
RECEIVABLE_ID = "AR-1"


class InjectedFailure(RuntimeError):
    """A failure that is not a business rejection — e.g. the DB dropping mid-request."""


def _ledger(fail_on: str, rows: Callable[[str], list[dict[str, Any]] | None]) -> FakeConnection:
    """Fake connection: answers by SQL text, raises on the first statement containing `fail_on`."""

    def answer(query: str, _params: Any) -> list[dict[str, Any]]:
        if fail_on in query:
            raise InjectedFailure(f"injected at: {fail_on}")
        return rows(query) or []

    return FakeConnection(answer)


def _statements(conn: FakeConnection) -> list[str]:
    """Statement heads in execution order, e.g. `UPDATE expenses`, `SELECT expenses FOR UPDATE`."""
    heads = []
    for query, _params in conn.executed:
        text = query.replace('"haetdeul".', "")
        verb = text.split()[0].upper()
        if verb == "INSERT":
            table = text.split("INTO", 1)[1].split()[0]
        elif verb == "UPDATE":
            table = text.split()[1]
        else:
            table = text.split("FROM", 1)[1].split()[0]
        lock = " FOR UPDATE" if "FOR UPDATE" in text else ""
        heads.append(f"{verb} {table}{lock}")
    return heads


def _assert_rolled_back(conn: FakeConnection, expected: list[str]) -> None:
    assert _statements(conn) == expected, (
        "the request did not reach the injected statement as planned"
    )
    assert conn.events == ["rollback"], (
        "a partial write must end in exactly one rollback and no commit"
    )


def test_credit_limit_change_rolls_back_the_closed_period_when_the_insert_fails():
    """앞 한도 기간을 끝낸(UPDATE) 뒤 새 한도 INSERT 가 터지면 끝낸 것도 되돌린다."""

    def rows(query: str) -> list[dict[str, Any]] | None:
        if ".partners WHERE partner_id" in query:
            return [{"?column?": 1}]
        if "partner_credit_limits" in query and "FOR UPDATE" in query:
            return [
                {
                    "partner_credit_limit_id": "PCL-1",
                    "effective_from": date(2026, 1, 1),
                    "effective_to": None,
                }
            ]
        return None

    conn = _ledger("INSERT INTO", rows)
    change = CreditLimitChange(
        partner_id="KIMCHI_FACTORY_001",
        credit_limit_krw=Decimal(10000000),
        effective_from=DAY,
        evidence_grade="SIM_FIXED",
        source_ref="contract-1",
        recorded_by="tester",
    )

    with pytest.raises(InjectedFailure):
        credit_limits.change_credit_limit(conn, change)

    _assert_rolled_back(
        conn,
        [
            "SELECT partners",
            "SELECT partner_credit_limits FOR UPDATE",
            "UPDATE partner_credit_limits",
            "INSERT partner_credit_limits",
        ],
    )


def test_expense_accrual_rolls_back_when_the_insert_fails():
    """운영비 발생은 INSERT 하나다 — 터지면 rollback 하고 예외를 그대로 올린다."""
    conn = _ledger("INSERT INTO", lambda _query: None)
    expense = ExpenseCreate(
        sim_run_id=RUN,
        expense_date=DAY,
        due_date=DAY,
        expense_category="OTHER",
        amount_krw=Decimal(10000),
        evidence_id="EV-1",
    )

    with pytest.raises(InjectedFailure):
        expenses.accrue_operating_expense(conn, expense)

    _assert_rolled_back(conn, ["INSERT expenses"])


def _expense_rows(query: str) -> list[dict[str, Any]] | None:
    if 'FROM "haetdeul".expenses' in query and "FOR UPDATE" in query:
        return [
            {
                "expense_id": EXPENSE_ID,
                "sim_run_id": RUN,
                "status": "ACCRUED",
                "amount_krw": Decimal(10000),
            }
        ]
    if 'FROM "haetdeul".finance_states' in query and "FOR UPDATE" in query:
        return [{"finance_state_id": STATE_ID, "current_cash_krw": Decimal(500000)}]
    if query.startswith('UPDATE "haetdeul".expenses'):
        return [{"expense_id": EXPENSE_ID}]  # one row changed
    return None


def test_expense_payment_rolls_back_the_paid_mark_when_the_cash_update_fails():
    """비용을 PAID 로 바꾼 뒤 현금 차감이 터지면 PAID 표시도 되돌린다.

    🔴 비용만 지급되고 현금은 그대로인 장부를 막는다.
    """
    conn = _ledger('UPDATE "haetdeul".finance_states', _expense_rows)
    request = ExpenseSettle(sim_run_id=RUN, financing_mode=MODE, paid_date=DAY)

    with pytest.raises(InjectedFailure):
        expenses.pay_accrued_expense(conn, EXPENSE_ID, request)

    _assert_rolled_back(
        conn,
        [
            "SELECT expenses FOR UPDATE",
            "SELECT finance_states FOR UPDATE",
            "UPDATE expenses",
            "UPDATE finance_states",
        ],
    )


def test_expense_cancellation_rolls_back_when_the_update_fails():
    conn = _ledger('UPDATE "haetdeul".expenses', _expense_rows)

    with pytest.raises(InjectedFailure):
        expenses.cancel_accrued_expense(conn, EXPENSE_ID, ExpenseCancel(sim_run_id=RUN))

    _assert_rolled_back(conn, ["SELECT expenses FOR UPDATE", "UPDATE expenses"])


def test_collection_record_rolls_back_the_event_when_applying_it_fails():
    """수금 사건 INSERT 와 그 적용(채권 · 현금 갱신)은 한 트랜잭션이다.

    🔴 사건만 남고 적용이 빠지면 다음 날 수금 단계가 같은 사건을 «이미 반영됨» 으로 읽는다.
    """

    def rows(query: str) -> list[dict[str, Any]] | None:
        if 'FROM "haetdeul".receivables' in query and "FOR UPDATE" in query:
            return [
                {
                    "receivable_id": RECEIVABLE_ID,
                    "sim_run_id": RUN,
                    "original_amount_krw": Decimal(10000),
                    "received_amount_krw": Decimal(0),
                    "outstanding_amount_krw": Decimal(10000),
                    "status": "OPEN",
                }
            ]
        if "INSERT INTO" in query and "master_collection_events" in query:
            return [{"inserted": 1}]  # rowcount 1 — the event row was written
        if 'FROM "haetdeul".finance_states' in query and "FOR UPDATE" in query:
            return [
                {
                    "finance_state_id": STATE_ID,
                    "sim_run_id": RUN,
                    "financing_mode": MODE,
                    "current_cash_krw": Decimal(500000),
                    "receivables_krw": Decimal(10000),
                }
            ]
        return None

    conn = _ledger('UPDATE "haetdeul".receivables', rows)
    change = ReceivableCollectionChange(
        sim_run_id=RUN,
        financing_mode=MODE,
        collection_date=DAY,
        receivable_id=RECEIVABLE_ID,
        amount_krw=Decimal(1000),
        source_ref="bank-1",
        recorded_by="tester",
    )

    with pytest.raises(InjectedFailure):
        collections.record_collection(conn, change)

    statements = _statements(conn)
    assert statements[:2] == ["SELECT receivables FOR UPDATE", "INSERT master_collection_events"]
    assert statements[-1] == "UPDATE receivables", (
        "the failure was not injected while applying the event"
    )
    assert conn.events == ["rollback"]


def test_cash_adjustment_rolls_back_the_adjustment_row_when_the_cash_update_fails():
    """조정 행 INSERT 뒤 현금 갱신이 터지면 조정 행도 되돌린다.

    🔴 조정 기록만 있고 현금이 그대로인 장부를 막는다.
    """

    def rows(query: str) -> list[dict[str, Any]] | None:
        if 'FROM "haetdeul".finance_states' in query and "FOR UPDATE" in query:
            return [{"finance_state_id": STATE_ID, "current_cash_krw": Decimal(500000)}]
        return None

    conn = _ledger('UPDATE "haetdeul".finance_states', rows)
    change = CashAdjustmentChange(
        sim_run_id=RUN,
        financing_mode=MODE,
        adjustment_date=DAY,
        direction="INFLOW",
        category="OWNER_INJECTION",
        amount_krw=Decimal(5000),
        source_ref="bank-2",
        recorded_by="tester",
    )

    with pytest.raises(InjectedFailure):
        cash_adjustments.apply_cash_adjustment(conn, change)

    _assert_rolled_back(
        conn,
        [
            "SELECT finance_states FOR UPDATE",
            "INSERT finance_cash_adjustments",
            "UPDATE finance_states",
        ],
    )
