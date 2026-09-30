"""매입안 상태와 실매입 합계 — 마스터 readmodel · domain 으로 옮긴 두 자리 (재구성 BL-012).

```text
master/readmodel/purchase_record.recorded_totals_by_plan   repository 합 → {(품목, 안 이름): 합계}
master/domain/plan_state.state_of                          승인 · 기록 두 사실 → 낱말
```

2026-09-29 전에는 합계 조립이 SQL 과 한 함수(`purchase_record_repository`)에 있었고
화면 둘이 그 repository 를 직접 불렀다. 판정은 `app/api/plan_state.py` 였다.

🔴 **실 DB 에 닿지 않는다.** repository 의 SQL 실행 자리(`fetch_all`)를 대역으로 바꾼다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.master.domain import plan_state
from app.master.readmodel import purchase_record
from app.master.readmodel.purchase_record import RecordedTotals, recorded_totals_by_plan
from tests.fake_core_db import patch_sql_helpers

AS_OF = date(2026, 4, 13)


@pytest.fixture
def rows(monkeypatch) -> dict[str, Any]:
    state: dict[str, Any] = {"rows": [], "calls": []}

    def fetch_all(query: Any, params: Any = None) -> list[dict[str, Any]]:
        state["calls"].append(params)
        return [dict(row) for row in state["rows"]]

    monkeypatch.setenv("DB_SCHEMA", "haetdeul")
    patch_sql_helpers(monkeypatch, "app.master.readmodel.purchase_record", fetch_all=fetch_all)
    return state


def test_recorded_totals_are_keyed_by_item_and_plan_label(rows):
    rows["rows"] = [
        {"item": "배추", "scenario_label": "보수", "quantity_kg": Decimal(500),
         "amount_krw": Decimal(275000)},
        #  정수 단가로 안 떨어지면 단가는 None — 금액이 정본이다
        {"item": "무", "scenario_label": "기본", "quantity_kg": Decimal(3),
         "amount_krw": Decimal(10)},
        #  소수 수량 — 단가를 어림하지 않는다
        {"item": "양파", "scenario_label": "공격", "quantity_kg": Decimal("1.5"),
         "amount_krw": Decimal(9)},
        #  금액은 원 단위로 반올림한다
        {"item": "양파", "scenario_label": "보수", "quantity_kg": Decimal(2),
         "amount_krw": Decimal("10.4")},
    ]

    got = recorded_totals_by_plan(sim_run_id="SIM-A", as_of=AS_OF)

    assert got == {
        ("배추", "보수"): RecordedTotals(qty_kg=500.0, amount_krw=275_000, unit_price=550),
        ("무", "기본"): RecordedTotals(qty_kg=3.0, amount_krw=10, unit_price=None),
        ("양파", "공격"): RecordedTotals(qty_kg=1.5, amount_krw=9, unit_price=None),
        ("양파", "보수"): RecordedTotals(qty_kg=2.0, amount_krw=10, unit_price=5),
    }
    #  🔴 축과 기준일을 둘 다 넘긴다 — 빼면 다른 걷기 · 다른 날의 기록이 붙는다
    assert rows["calls"] == [{"sim_run_id": "SIM-A", "as_of": AS_OF}]


def test_no_records_give_empty_totals(rows):
    assert recorded_totals_by_plan(sim_run_id="SIM-A", as_of=AS_OF) == {}


def test_read_errors_propagate(monkeypatch):
    """⚠️ 삼키는 것은 화면의 태도다(빈 표로 화면을 띄운다) — readmodel 은 숨기지 않는다."""

    def failing_read(*_: Any):
        raise RuntimeError("DB 가 죽었다")

    # ★ 2026-09-30 재구성 BL-018: 조회 함수가 연결을 스스로 빌리지 않는다 — readmodel 이 빌린
    #   연결에서 읽다가 죽는 자리를 꽂는다.
    monkeypatch.setenv("DB_SCHEMA", "haetdeul")
    patch_sql_helpers(monkeypatch, purchase_record, fetch_all=failing_read)
    with pytest.raises(RuntimeError, match="DB 가 죽었다"):
        recorded_totals_by_plan(sim_run_id="SIM-A", as_of=AS_OF)


@pytest.mark.parametrize(
    ("approved", "recorded", "expected"),
    [
        (False, False, "후보"),
        #  🔴 승인 안 된 안에 기록이 붙어도 「후보」다 — 승인이 먼저다
        (False, True, "후보"),
        (True, False, "승인됨"),
        (True, True, "매입 기록됨"),
    ],
)
def test_state_of_depends_on_approval_and_record(approved, recorded, expected):
    assert plan_state.state_of(approved=approved, recorded=recorded) == expected
    assert expected in plan_state.PLAN_STATES


def test_there_are_four_plan_states():
    assert plan_state.PLAN_STATES == ("후보", "승인됨", "매입 기록됨", "반려")
