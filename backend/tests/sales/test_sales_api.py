from datetime import UTC, date, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from tests.sales.sales_fake_connection import lend

client = TestClient(app)


def _fake_run(**overrides) -> dict:
    row = {
        "run_id": uuid4(),
        "cycle": "PROCUREMENT",
        "as_of": date(2026, 8, 21),
        "snapshot_id": "T0-20260821-01",
        "runtime_status": "READY",
        "request_payload": {"item": "배추"},
        "response_payload": {"agent": "sales"},
        "created_at": datetime(2026, 8, 21, 9, 0, 0, tzinfo=UTC),
    }
    row.update(overrides)
    return row


def test_sales_openapi_exposes_proposal_and_drops_legacy_endpoints():
    schema = client.get("/openapi.json").json()

    assert "/sales/proposal" in schema["paths"]
    assert "/sales/runs" in schema["paths"]
    assert "/sales/dashboard" not in schema["paths"]
    assert "/sales/procurement" not in schema["paths"]
    assert "/sales/allocation" not in schema["paths"]


def test_list_runs_applies_filter(monkeypatch):
    captured = {}

    def fake_list(conn, **kwargs):
        captured["conn"] = conn
        captured.update(kwargs)
        return [_fake_run()]

    #  ★ 2026-09-29 BL-013: 조회는 `readmodel/runs` 가 조회 연결을 빌려 repository 에 넘긴다.
    lent = lend(monkeypatch)
    monkeypatch.setattr("app.sales.readmodel.runs.list_sales_agent_runs", fake_list)

    response = client.get("/sales/runs", params={"cycle": "PROCUREMENT", "limit": 10})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["cycle"] == "PROCUREMENT"
    assert captured["cycle"] == "PROCUREMENT"
    assert captured["limit"] == 10
    assert captured["conn"] is lent
    assert lent.borrows == ["read"]
    assert lent.events == ["returned:read"]


def test_get_run_single(monkeypatch):
    run = _fake_run()
    lend(monkeypatch)
    monkeypatch.setattr("app.sales.readmodel.runs.get_sales_agent_run", lambda _conn, run_id: run)

    response = client.get(f"/sales/runs/{run['run_id']}")

    assert response.status_code == 200
    assert response.json()["run_id"] == str(run["run_id"])


def test_get_run_missing_is_404(monkeypatch):
    def fake_get(_conn, run_id):
        raise LookupError("not found")

    lent = lend(monkeypatch)
    monkeypatch.setattr("app.sales.readmodel.runs.get_sales_agent_run", fake_get)

    response = client.get(f"/sales/runs/{uuid4()}")
    assert response.status_code == 404
    #  ★ 못 찾아도 빌린 조회 연결은 돌려준다.
    assert lent.events == ["returned:read"]


def test_list_runs_limit_out_of_range_is_422():
    response = client.get("/sales/runs", params={"limit": 999})
    assert response.status_code == 422
