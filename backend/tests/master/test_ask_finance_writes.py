"""마스터 ask 의 재무 쓰기가 재무 라우터와 **같은 service · 같은 거절 · 같은 트랜잭션**인가.

★ 2026-09-29 재구성 BL-014: 전에는 ask 가 재무 라우터 핸들러(`register_credit_limit` ·
  `create_operating_expense` 등)를 파이썬 함수로 불러, 핸들러의 `HTTPException` 이
  `POST /master/ask/execute` 응답으로 그대로 나갔다. 지금은 두 입구가 같은 재무 service
  (`app/finance/service/`)를 부르고, service 가 낸 `FinanceWriteRejected` 를 **각자** 옮긴다 —
  재무 라우터는 상태 코드로, ask 는 마스터 라우터가 접는 거절(`LookupError` · `DecisionRejected`)로.

여기서 재는 것:

1. 같은 업무 거절이 두 입구에서 **같은 상태 코드 · 같은 문장**으로 나온다 (404 · 409 · 422).
2. 두 입구가 연결을 **하나** 빌리고, 받지 않은 요청은 rollback · 받은 요청은 commit 한 번이다.
3. 받은 요청에서 두 입구가 **같은 SQL 을 같은 순서로** 실행한다.

★ DB 를 치지 않는다 — 가짜 연결(`tests/finance/finance_fake_connection.py`)이 SQL 문면을 보고
  행을 돌려준다. SQL 은 진짜 repository 가 짓고, 판정 · 거절 옮기기는 진짜 코드가 돈다.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.finance import deps as finance_http
from app.api.master.ask import router as master_router
from app.core.settings import SHOWN_SIM_RUN_ID
from app.finance.schemas.write_rejection import FinanceWriteRejected
from app.main import app as whole_app
from app.master.service import ask_domain_actions
from tests.finance.finance_fake_connection import FakeConnection, lend

AS_OF = "2026-09-17"
PARTNER = "KIMCHI_FACTORY_001"
EXPENSE = "EXP-1"
ACTOR = "tester"


@pytest.fixture
def master() -> TestClient:
    app = FastAPI()
    app.include_router(master_router)
    return TestClient(app)


@pytest.fixture
def finance() -> TestClient:
    return TestClient(whole_app)


class _Ledger:
    """가짜 연결의 답. 문면으로 가르고, 칸마다 돌려줄 행을 바꿔 끼운다."""

    def __init__(self) -> None:
        self.partners: list[dict[str, Any]] = [{"?column?": 1}]
        self.active_limits: list[dict[str, Any]] = []
        self.expenses: list[dict[str, Any]] = []
        self.states: list[dict[str, Any]] = []

    def __call__(self, query: str, _params: Any) -> list[dict[str, Any]]:
        if ".partners WHERE partner_id" in query:
            return self.partners
        if "FROM \"haetdeul\".partner_credit_limits" in query and "FOR UPDATE" in query:
            return self.active_limits
        if "FROM \"haetdeul\".expenses" in query and "FOR UPDATE" in query:
            return self.expenses
        if "FROM \"haetdeul\".finance_states" in query and "FOR UPDATE" in query:
            return self.states
        return []


@pytest.fixture
def ledger(monkeypatch) -> tuple[_Ledger, FakeConnection]:
    answer = _Ledger()
    conn = lend(monkeypatch, answer)
    #  ask 의 거래처 찾기(`_partner_id`)가 보는 판매 조회 — 거래처 하나가 이 코드로 있다.
    monkeypatch.setattr(
        ask_domain_actions,
        "get_console_partners",
        lambda **_kwargs: SimpleNamespace(
            rows=[SimpleNamespace(partner_id=PARTNER, partner_name="김치공장")]
        ),
    )
    return answer, conn


def _ask(master: TestClient, action: str, slots: dict[str, Any]) -> Any:
    return master.post(
        "/master/ask/execute",
        json={
            "intent": {
                "action": "DOMAIN_ACTION",
                "agents": [],
                "confidence": "HIGH",
                "domain_action": action,
                "slots": slots,
            },
            "as_of": AS_OF,
            "policy_version": "v1.3",
            "actor": ACTOR,
        },
    )


def _reset(conn: FakeConnection) -> None:
    conn.executed.clear()
    conn.events.clear()
    conn.borrows.clear()


def _sql(conn: FakeConnection) -> list[str]:
    return [query for query, _params in conn.executed]


_LIMIT_HTTP = {
    "partner_id": PARTNER,
    "credit_limit_krw": "10000000",
    "effective_from": "2026-09-17",
    "evidence_grade": "SIM_FIXED",
    "source_ref": "계약서-1",
    "recorded_by": ACTOR,
}
_LIMIT_ASK = {
    "partner_ref": PARTNER,
    "credit_limit": "10000000",
    "effective_from": "2026-09-17",
    "evidence_grade": "SIM_FIXED",
    "source_ref": "계약서-1",
}


def _both(master: TestClient, finance: TestClient, conn: FakeConnection, http, ask):
    """두 입구를 차례로 부르고 (응답, 그 입구가 연결에 남긴 기록)을 돌려준다."""
    _reset(conn)
    via_http = http()
    http_trace = (list(conn.borrows), list(conn.events), _sql(conn))
    _reset(conn)
    via_ask = ask()
    ask_trace = (list(conn.borrows), list(conn.events), _sql(conn))
    return via_http, http_trace, via_ask, ask_trace


# ── 1. 같은 거절 · 같은 문장 ────────────────────────────────────────────────


def test_an_unknown_partner_is_the_same_404_from_both_doors(master, finance, ledger):
    answer, conn = ledger
    answer.partners = []

    via_http, http_trace, via_ask, ask_trace = _both(
        master,
        finance,
        conn,
        lambda: finance.post("/finance/credit-limits", json=_LIMIT_HTTP),
        lambda: _ask(master, "FINANCE_CREDIT_LIMIT_UPSERT", _LIMIT_ASK),
    )

    assert via_http.status_code == via_ask.status_code == 404
    assert via_http.json()["detail"] == via_ask.json()["detail"] == "거래처를 찾지 못했습니다."
    #  두 입구 모두 연결 하나 · rollback · commit 없음. 실행한 SQL 도 같다.
    assert http_trace == ask_trace
    borrows, events, _ = http_trace
    assert borrows == ["write"]
    assert "rollback" in events and "commit" not in events


def test_an_overlapping_period_is_the_same_409_from_both_doors(master, finance, ledger):
    answer, conn = ledger
    answer.active_limits = [
        {
            "partner_credit_limit_id": "PCL-1",
            "effective_from": date(2026, 9, 20),
            "effective_to": None,
        }
    ]

    via_http, http_trace, via_ask, ask_trace = _both(
        master,
        finance,
        conn,
        lambda: finance.post("/finance/credit-limits", json=_LIMIT_HTTP),
        lambda: _ask(master, "FINANCE_CREDIT_LIMIT_UPSERT", _LIMIT_ASK),
    )

    assert via_http.status_code == via_ask.status_code == 409
    assert via_http.json()["detail"] == via_ask.json()["detail"]
    assert via_ask.json()["detail"] == "새 적용일은 현재 한도 적용일보다 뒤여야 합니다."
    assert http_trace == ask_trace
    assert "rollback" in http_trace[1] and "commit" not in http_trace[1]


@pytest.mark.parametrize(
    ("rows", "status_code", "detail"),
    [
        ([], 404, f"비용을 찾을 수 없습니다: {EXPENSE}"),
        (
            [{"expense_id": EXPENSE, "sim_run_id": SHOWN_SIM_RUN_ID, "status": "PAID"}],
            409,
            "이미 지급된 비용은 취소할 수 없습니다 — 환입은 별도 정책이 필요합니다.",
        ),
    ],
    ids=["missing", "paid"],
)
def test_an_expense_cancel_rejection_is_the_same_from_both_doors(
    master, finance, ledger, rows, status_code, detail
):
    answer, conn = ledger
    answer.expenses = rows

    via_http, http_trace, via_ask, ask_trace = _both(
        master,
        finance,
        conn,
        lambda: finance.post(
            f"/finance/expenses/{EXPENSE}/cancel", json={"sim_run_id": SHOWN_SIM_RUN_ID}
        ),
        lambda: _ask(master, "FINANCE_EXPENSE_CANCEL", {"expense_id": EXPENSE}),
    )

    assert via_http.status_code == via_ask.status_code == status_code
    assert via_http.json()["detail"] == via_ask.json()["detail"] == detail
    assert http_trace == ask_trace
    assert "rollback" in http_trace[1] and "commit" not in http_trace[1]


def test_a_missing_state_on_the_paid_date_is_the_same_409_from_both_doors(
    master, finance, ledger
):
    """지급일 재무 상태가 없으면 두 입구 모두 같은 409 · 같은 문장이다 (문장은 재무 소유)."""
    from app.finance.domain import messages

    answer, conn = ledger
    answer.expenses = [
        {
            "expense_id": EXPENSE,
            "sim_run_id": SHOWN_SIM_RUN_ID,
            "status": "ACCRUED",
            "amount_krw": 1000,
        }
    ]
    answer.states = []

    via_http, http_trace, via_ask, ask_trace = _both(
        master,
        finance,
        conn,
        lambda: finance.post(
            f"/finance/expenses/{EXPENSE}/settle",
            json={
                "sim_run_id": SHOWN_SIM_RUN_ID,
                "financing_mode": "LOAN_BASELINE",
                "paid_date": AS_OF,
            },
        ),
        lambda: _ask(
            master,
            "FINANCE_EXPENSE_SETTLE",
            {"expense_id": EXPENSE, "financing_mode": "LOAN_BASELINE", "paid_date": AS_OF},
        ),
    )

    assert via_http.status_code == via_ask.status_code == 409
    assert via_http.json()["detail"] == via_ask.json()["detail"]
    assert via_ask.json()["detail"] == messages.STATE_NOT_READY_ON_PAID_DATE
    assert http_trace == ask_trace


# ── 2. 받은 요청 — 같은 SQL · commit 한 번 ─────────────────────────────────


def test_an_accepted_credit_limit_runs_the_same_sql_and_commits_once(master, finance, ledger):
    _answer, conn = ledger

    via_http, http_trace, via_ask, ask_trace = _both(
        master,
        finance,
        conn,
        lambda: finance.post("/finance/credit-limits", json=_LIMIT_HTTP),
        lambda: _ask(master, "FINANCE_CREDIT_LIMIT_UPSERT", _LIMIT_ASK),
    )

    assert via_http.status_code == 201
    assert via_ask.status_code == 200
    assert via_http.json()["credit_limit_krw"] == "10000000"
    assert via_ask.json()["domain_result"]["data"]["credit_limit_krw"] == "10000000"
    assert http_trace == ask_trace
    borrows, events, statements = http_trace
    assert borrows == ["write"]
    assert events == ["commit", "returned:write"]
    assert [s.split()[0] for s in statements] == ["SELECT", "SELECT", "INSERT"]


# ── 3. 거절 사유 → 상태 코드 옮기기가 두 입구에서 같다 ──────────────────────────


@pytest.mark.parametrize(
    ("reason", "status_code"),
    [("NOT_FOUND", 404), ("CONFLICT", 409), ("INVALID", 422)],
)
def test_every_rejection_reason_maps_to_the_same_status(master, monkeypatch, reason, status_code):
    """service 가 낸 사유 셋이 재무 라우터 · ask 에서 같은 상태 코드와 같은 문장이 된다.

    ★ `INVALID` 는 ask 가 먼저 입력을 되묻는 자리라 실제 요청으로는 닿지 않는다 — 옮기는
      두 자리(`api/finance/deps.rejected` · `ask_service._finance_write` → 마스터 라우터)를
      같은 사유로 직접 잰다.
    """
    error = FinanceWriteRejected(reason, f"{reason} 문장")
    http = finance_http.rejected(error)

    def rejecting(_conn, _expense_id, _request):
        raise error

    monkeypatch.setattr(ask_domain_actions, "cancel_accrued_expense", rejecting)
    lend(monkeypatch)
    via_ask = _ask(master, "FINANCE_EXPENSE_CANCEL", {"expense_id": EXPENSE})

    assert http.status_code == via_ask.status_code == status_code
    assert http.detail == via_ask.json()["detail"] == f"{reason} 문장"
