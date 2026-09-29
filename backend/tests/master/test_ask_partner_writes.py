"""마스터 ask 의 거래처 쓰기가 판매 라우터와 **같은 service · 같은 거절**을 내는가.

★ 2026-09-29 재구성 BL-013: 전에는 ask 가 판매 라우터 핸들러(`add_partner_profile` ·
  `edit_partner_profile`)를 파이썬 함수로 불러, 핸들러의 `HTTPException` 이
  `POST /master/ask/execute` 응답으로 그대로 나갔다. 지금은 두 입구가 같은
  `app.sales.service.partners` 를 부르고, 업무 예외를 **각자** 옮긴다 — 판매 라우터는
  상태 코드로, ask 는 마스터 라우터가 접는 거절(`DecisionRejected` · `LookupError`)로.

여기서 재는 것: 같은 업무 예외가 두 입구에서 **같은 상태 코드 · 같은 문장**으로 나오는가.
화면이 보던 응답이 이 이동으로 바뀌지 않았다는 증거다.

★ DB 를 치지 않는다 — 판매 service 의 저장 한 자리(`write_partner`)와
  거래처 찾기(`_partner_id` 가 부르는 판매 조회)만 대역으로 바꾼다. 입력 검사 · 예외 옮기기는
  진짜 코드가 돈다.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.settings import MissingDatabaseEnvironment
from app.main import app as whole_app
from app.master.router import router
from tests.sales.sales_fake_connection import lend

AS_OF = "2026-09-17"
PARTNER = "KIMCHI_FACTORY_001"


@pytest.fixture
def master() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def sales() -> TestClient:
    return TestClient(whole_app)


@pytest.fixture
def fake_partner_sql(monkeypatch):
    """판매 SQL 실행 자리의 대역. 값을 바꿔 가며 «이미 있다» · «없다» 를 만든다.

    ★ SQL 은 진짜 repository 가 짓는다(`insert_partner_statement` · `update_partner_statement`).
      대역은 실행(`write_partner`)만 바꾼다 — 행이 없으면 종전 헬퍼처럼 `RuntimeError` 다.
    """
    state = SimpleNamespace(insert=None, update=None, calls=[])

    def write_partner(_conn, write):
        kind = "insert" if "INSERT INTO" in repr(write.query) else "update"
        state.calls.append((kind, repr(write.query), list(write.params)))
        row = state.insert if kind == "insert" else state.update
        if row is None:
            raise RuntimeError("Database write did not return a row")
        return row

    monkeypatch.setattr("app.sales.repository.partners.get_db_schema", lambda: "haetdeul")
    monkeypatch.setattr("app.sales.service.partners.write_partner", write_partner)
    #  ask 의 거래처 찾기(`_partner_id`)가 보는 판매 조회 — 거래처 하나가 이 이름으로 있다.
    monkeypatch.setattr(
        "app.master.ask_service.get_console_partners",
        lambda **_kwargs: SimpleNamespace(
            rows=[SimpleNamespace(partner_id=PARTNER, partner_name="김치공장")]
        ),
    )
    lend(monkeypatch)
    return state


def _ask(master: TestClient, action: str, slots: dict) -> object:
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
            "actor": "tester",
        },
    )


_NEW = {"partner_id": PARTNER, "partner_name": "김치공장", "partner_type": "CUSTOMER"}


def test_a_duplicate_code_is_the_same_409_from_both_doors(master, sales, fake_partner_sql):
    """🔴 이미 있는 코드 — 판매 라우터와 ask 가 같은 409 · 같은 문장이다."""
    fake_partner_sql.insert = None  # `DO NOTHING` — 행이 안 나온다

    via_sales = sales.post("/sales/partners", json=_NEW)
    via_ask = _ask(master, "PARTNER_CREATE", _NEW)

    assert via_sales.status_code == via_ask.status_code == 409
    assert via_sales.json()["detail"] == via_ask.json()["detail"]
    assert via_ask.json()["detail"] == f"이미 등록된 거래처 코드입니다: {PARTNER}"
    #  두 입구가 같은 저장 자리에 같은 값을 넘겼다.
    assert fake_partner_sql.calls[0] == fake_partner_sql.calls[1]


def test_a_type_the_table_rejects_is_the_same_422_from_both_doors(master, sales, fake_partner_sql):
    bad = {**_NEW, "partner_type": "고객"}

    via_sales = sales.post("/sales/partners", json=bad)
    via_ask = _ask(master, "PARTNER_CREATE", bad)

    assert via_sales.status_code == via_ask.status_code == 422
    assert via_sales.json()["detail"] == via_ask.json()["detail"]
    assert "거래처 유형" in via_ask.json()["detail"]
    #  🔴 거절한 입력은 저장 자리까지 가지 않는다.
    assert fake_partner_sql.calls == []


def test_an_unknown_partner_is_the_same_404_from_both_doors(master, sales, fake_partner_sql):
    """🔴 고칠 행이 없다 — 판매 라우터와 ask 가 같은 404 · 같은 문장이다."""
    fake_partner_sql.update = None

    via_sales = sales.patch(f"/sales/partners/{PARTNER}/profile", json={"partner_name": "새"})
    via_ask = _ask(master, "PARTNER_UPDATE", {"partner_ref": PARTNER, "partner_name": "새"})

    assert via_sales.status_code == via_ask.status_code == 404
    assert via_sales.json()["detail"] == via_ask.json()["detail"] == "거래처를 찾지 못했습니다."


def test_a_setting_error_while_writing_is_the_same_409_from_both_doors(
    master, sales, fake_partner_sql, monkeypatch
):
    """⚠️ 종전 동작 그대로 — 연결을 열다 난 설정 누락도 두 입구에서 같은 409 · 같은 문장이다
    (좁힐지는 설계서 §변경 제안)."""

    @contextmanager
    def borrow_without_connection_settings():
        raise MissingDatabaseEnvironment("Missing required database environment variables: DB_HOST")
        yield  # pragma: no cover

    monkeypatch.setattr("app.core.db.connection", borrow_without_connection_settings)

    via_sales = sales.post("/sales/partners", json=_NEW)
    via_ask = _ask(master, "PARTNER_CREATE", _NEW)

    assert via_sales.status_code == via_ask.status_code == 409
    assert via_sales.json()["detail"] == via_ask.json()["detail"]
    assert fake_partner_sql.calls == []


def test_a_created_partner_comes_back_as_the_stored_row_through_ask(master, fake_partner_sql):
    fake_partner_sql.insert = {
        "partner_id": PARTNER,
        "partner_name": "김치공장",
        "partner_type": "CUSTOMER",
        "client_type": None,
        "factory_region": None,
        "factory_city": None,
        "factory_area": None,
        "sales_collection_days": None,
        "pricing_contract_type": None,
        "active": True,
        "provisional": False,
        "note": None,
    }

    response = _ask(master, "PARTNER_CREATE", _NEW)

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "DOMAIN_ACTION_EXECUTED"
    assert f"김치공장({PARTNER}) 거래처를 등록했습니다." in str(body)
