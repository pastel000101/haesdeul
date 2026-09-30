from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.contracts.aging import classify_receivable_aging
from app.finance.readmodel.console_receivables import get_console_receivables
from tests.finance.finance_fake_connection import lend

AS_OF = date(2026, 9, 11)


@pytest.mark.parametrize(
    "days,bucket",
    [(0, "CURRENT"), (1, "1_7"), (7, "1_7"), (8, "8_30"), (30, "8_30"), (31, "30_PLUS")],
)
def test_aging_boundaries(days, bucket):
    got, overdue = classify_receivable_aging(
        outstanding_amount_krw=Decimal(1), due_date=AS_OF - timedelta(days=days), as_of=AS_OF
    )
    assert got == bucket
    assert overdue == (0 if days == 0 else days)


def test_paid_is_not_missing_and_none_is_rejected():
    assert classify_receivable_aging(
        outstanding_amount_krw=Decimal(0), due_date=AS_OF, as_of=AS_OF
    ) == ("PAID", None)
    with pytest.raises(ValueError):
        classify_receivable_aging(outstanding_amount_krw=None, due_date=AS_OF, as_of=AS_OF)


def test_console_receivables_never_mix_runs(monkeypatch):
    #  ⚠️ **자리로 찾지 않는다.** 수금 복원 JOIN 이 생기면서 기준일이 실행 축보다 먼저
    #    오게 됐다. 자리에 기대면 SQL 을 고칠 때마다 이 검사가 이유 없이 빨개진다.
    def rows(_query, params):
        run = next(value for value in params if str(value).startswith("SIM-CONSOLE"))
        return [
            {
                "receivable_id": "AR-A" if run == "SIM-CONSOLE-A" else "AR-B",
                "sale_id": "S",
                "partner_id": "P",
                "partner_name": "P",
                "original_amount_krw": Decimal(10),
                "received_amount_krw": Decimal(0),
                "outstanding_amount_krw": Decimal("10" if run == "SIM-CONSOLE-A" else "20"),
                "due_date": AS_OF,
                "status": "OPEN",
            }
        ]

    #  2026-09-29 재구성 BL-014: 화면 조회가 조회 연결을 빌려 repository 에 넘긴다 — 가짜 연결의
    #  답을 `rows` 가 한다.
    conn = lend(monkeypatch, rows)
    a = get_console_receivables(sim_run_id="SIM-CONSOLE-A", as_of=AS_OF)
    b = get_console_receivables(sim_run_id="SIM-CONSOLE-B", as_of=AS_OF)
    assert a.rows[0].receivable_id == "AR-A" and a.summary.total_outstanding_krw == Decimal(10)
    assert b.rows[0].receivable_id == "AR-B" and b.summary.total_outstanding_krw == Decimal(20)
    assert conn.borrows == ["read", "read"]  # 화면 응답마다 조회 연결 하나
