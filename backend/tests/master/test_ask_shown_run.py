"""「마스터에게 묻기」는 요청에 실린 실행 ID 하나로 조회 · 쓰기 · 매입 실행을 하고, 상태 조회
이력은 정본 축으로 안 적는다.

요청이 실행 ID 를 비우면 HTTP 입구(`app/api/master/ask.py`)가 백엔드 기준값
(`app/core/settings.py`)으로 채운다. 서비스는 받은 값만 쓴다.

실 DB 에 닿지 않는다. 부서 포트 · 조회 · 쓰기는 대역이고 적재 함수는 가로챈다.

```text
① 상태 조회 봉투의 실행 ID = 요청의 실행 ID (번인이 아니다), 기준일은 요청 그대로
② 상태 조회 이력 행은 정본 축으로 안 적힌다 (None)
③ 응답 note 에 실제로 쓴 실행 · 기준일이 실린다
④ 판매 조회가 분류 사전 · 검증 · 조회 흐름에서 열려 있다
⑤ HTTP 입구: 실행 ID 를 비운 요청만 그때의 기준값으로 채우고, 준 값은 그대로 넘긴다
⑥ 부서 조회(재무 보고서 포함) · 쓰기 · 매입 실행이 모두 같은 요청 실행 ID 를 쓴다
⑦ 서비스는 실행 ID 없는 요청을 다른 값으로 메우지 않고 멈춘다
```
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.master import ask as ask_entry
from app.contracts.envelope import AgentReply, AgentRequest, ExecutionMetadata
from app.core import settings
from app.master.domain.sim_run import BURN_IN_SIM_RUN_ID
from app.master.llm.runtime import SYSTEM_PROMPT, _clarification, validate_intent
from app.master.llm.schemas import DomainSlots, Intent
from app.master.registry import wiring as registry_wiring
from app.master.schemas.ask import AskExecuteRequest, AskRequest
from app.master.service import ask, ask_domain_actions
from app.master.service import persistence as service_persistence

AS_OF_A = date(2026, 1, 13)
AS_OF_B = date(2026, 1, 20)
#: 요청이 싣는 실행 ID. 기준값과 다른 값이라, 서비스가 기준값을 다시 읽으면 잡힌다.
RUN = "SIM-TEST-PASSED"
#: 기준값 대역. HTTP 입구가 import 시점의 사본을 들고 있으면 이 값을 못 본다.
SUBSTITUTE = "SIM-TEST-SUBSTITUTE"


def _spy_port(seen: list[AgentRequest], payload: dict):
    def port(request: AgentRequest):
        seen.append(request)
        reply = AgentReply(
            request_id=request.context.request_id,
            as_of=request.context.as_of,
            agent=request.agent,
            mode=request.mode,
            run_id=f"{request.agent.upper()}-{request.call_seq}",
            runtime_status="READY",
            business_status="ok",
            payload=payload,
        )
        meta = ExecutionMetadata(
            run_id=reply.run_id,
            request_id=request.context.request_id,
            agent=request.agent,
            used_tools=("status_tool",),
            tool_order=(1,),
        )
        return reply, meta

    return port


@pytest.fixture(autouse=True)
def clean_wiring():
    registry_wiring.reset()
    yield
    registry_wiring.reset()


@pytest.fixture
def recorded(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    monkeypatch.setattr(service_persistence, "record_status", lambda **kw: calls.append(kw))
    return calls


def _status(agent: str, as_of: date, sim_run_id: str = RUN):
    return ask.execute(
        AskExecuteRequest(
            intent=Intent(action="STATUS_QUERY", agents=[agent], confidence="HIGH"),
            as_of=as_of,
            policy_version="v1.3",
            sim_run_id=sim_run_id,
        )
    )


# ── ① ~ ④ 상태 조회 ─────────────────────────────────────────────────────


def test_조회_봉투는_요청의_실행을_읽고_기준일을_그대로_넘긴다(recorded):
    seen: list[AgentRequest] = []
    registry_wiring.register("finance", _spy_port(seen, {"available_cash": 1}))

    _status("finance", AS_OF_A)
    _status("finance", AS_OF_B)

    assert [r.context.sim_run_id for r in seen] == [RUN, RUN]
    assert RUN != BURN_IN_SIM_RUN_ID
    assert [r.context.as_of for r in seen] == [AS_OF_A, AS_OF_B]


def test_조회_이력_행은_정본_축으로_안_적힌다(recorded):
    registry_wiring.register("finance", _spy_port([], {"available_cash": 1}))

    _status("finance", AS_OF_A)

    assert len(recorded) == 1
    assert recorded[0]["sim_run_id"] is None
    assert recorded[0]["as_of"] == AS_OF_A


def test_응답에_보고_있는_실행과_기준일이_실린다(recorded):
    registry_wiring.register("finance", _spy_port([], {"available_cash": 1}))

    response = _status("finance", AS_OF_B)

    assert response.outcome == "STATUS_ANSWERED"
    assert response.note == f"보고 있는 실행: {RUN} · 기준일: {AS_OF_B.isoformat()}"


def test_판매는_분류_사전에_있고_검증을_통과한다():
    assert "  sales  " in SYSTEM_PROMPT
    raw = json.dumps({"action": "STATUS_QUERY", "agents": ["sales"], "confidence": "HIGH"})
    intent = validate_intent(raw, "판매 진행 상황 알려줘")
    assert intent.agents == ["sales"]
    low = Intent(action="STATUS_QUERY", agents=["sales"], confidence="LOW")
    assert "판매" in _clarification(low)


def test_판매_조회가_같은_흐름으로_답한다(recorded):
    seen: list[AgentRequest] = []
    registry_wiring.register("sales", _spy_port(seen, {"recent_runs": []}))

    response = _status("sales", AS_OF_A)

    assert response.status.status_code == "S1_ANSWERED"
    assert [(r.agent, r.mode, r.context.sim_run_id) for r in seen] == [
        ("sales", "STATUS_QUERY", RUN)
    ]


# ── ⑤ HTTP 입구 ─────────────────────────────────────────────────────────


@pytest.fixture
def entry(monkeypatch) -> tuple[TestClient, list[tuple[str, str | None]]]:
    """두 입구가 서비스에 넘기는 실행 ID 를 잡는다. 기준값은 대역으로 바꿔 둔다."""
    seen: list[tuple[str, str | None]] = []

    def 묻기(request: AskRequest) -> Any:
        seen.append(("ask", request.sim_run_id))
        raise LookupError("멈춤")

    def 실행(request: AskExecuteRequest) -> Any:
        seen.append(("execute", request.sim_run_id))
        raise LookupError("멈춤")

    monkeypatch.setattr(ask_entry, "run_ask", 묻기)
    monkeypatch.setattr(ask_entry, "run_ask_execute", 실행)
    monkeypatch.setattr(settings, "SHOWN_SIM_RUN_ID", SUBSTITUTE)
    app = FastAPI()
    app.include_router(ask_entry.router)
    return TestClient(app, raise_server_exceptions=False), seen


_ASK = {"utterance": "지금 자금 상황 알려줘", "as_of": "2026-09-17", "policy_version": "v1.3"}
_EXECUTE = {
    "intent": {"action": "STATUS_QUERY", "agents": ["finance"], "confidence": "HIGH"},
    "as_of": "2026-09-17",
    "policy_version": "v1.3",
}


def test_입구는_비운_실행만_그때의_기준값으로_채운다(entry):
    client, seen = entry

    client.post("/master/ask", json=_ASK)
    client.post("/master/ask", json={**_ASK, "sim_run_id": "SIM-OTHER"})
    client.post("/master/ask/execute", json=_EXECUTE)
    client.post("/master/ask/execute", json={**_EXECUTE, "sim_run_id": "SIM-OTHER"})

    assert seen == [
        ("ask", SUBSTITUTE),
        ("ask", "SIM-OTHER"),
        ("execute", SUBSTITUTE),
        ("execute", "SIM-OTHER"),
    ]


def test_입구는_빈_글자_실행_ID_를_받지_않는다(entry):
    client, seen = entry

    assert client.post("/master/ask", json={**_ASK, "sim_run_id": ""}).status_code == 422
    blank = {**_EXECUTE, "sim_run_id": ""}
    assert client.post("/master/ask/execute", json=blank).status_code == 422
    assert seen == []


# ── ⑥ 부서 조회 · 쓰기 · 매입 실행 ──────────────────────────────────────


def _domain(action: str, **slots: Any) -> Intent:
    return Intent(
        action="DOMAIN_ACTION",
        agents=[],
        confidence="HIGH",
        domain_action=action,
        slots=DomainSlots(**slots),
    )


def _capture(seen: list[tuple[str, str]], name: str, result: Any = None):
    def 대역(*args: Any, **kwargs: Any) -> Any:
        seen.append((name, kwargs["sim_run_id"]))
        return result

    return 대역


class _멈춤(Exception):
    """넘긴 실행 ID 를 잡은 뒤 답 조립까지 가지 않으려고 던진다."""


def _stop(seen: list[tuple[str, str]], name: str):
    def 대역(*args: Any, **kwargs: Any) -> Any:
        seen.append((name, kwargs["sim_run_id"]))
        raise _멈춤

    return 대역


def test_재무_보고서와_다른_부서_조회가_같은_실행을_쓴다(monkeypatch):
    seen: list[tuple[str, str]] = []
    partner = SimpleNamespace(partner_id="P-1", partner_name="거래처")
    for name in (
        "get_finance_dashboard",
        "get_console_credit",
        "get_console_sales_proposals",
        "render_finance_chat_report",
        "render_sales_chat_report",
        "render_logistics_chat_report",
    ):
        monkeypatch.setattr(ask_domain_actions, name, _stop(seen, name))
    #  거래처 찾기는 답을 돌려줘야 그 뒤 조회까지 간다.
    monkeypatch.setattr(
        ask_domain_actions,
        "get_console_partners",
        _capture(seen, "get_console_partners", SimpleNamespace(rows=[partner])),
    )

    for intent in (
        _domain("FINANCE_SUMMARY_GET"),
        _domain("FINANCE_CREDIT_LIMIT_GET", partner_ref="P-1"),
        _domain("FINANCE_REPORT_GENERATE", period="TODAY"),
        _domain("SALES_PROPOSALS_TODAY"),
        _domain("SALES_REPORT_GENERATE", period="TODAY"),
        _domain("LOGISTICS_REPORT_GENERATE", period="TODAY"),
    ):
        with pytest.raises(_멈춤):
            ask_domain_actions.run_domain_action(
                intent, as_of=AS_OF_A, policy_version="v1.3", request_id="REQ-1", sim_run_id=RUN
            )

    assert [name for name, _ in seen] == [
        "get_finance_dashboard",
        "get_console_partners",
        "get_console_credit",
        "render_finance_chat_report",
        "get_console_sales_proposals",
        "render_sales_chat_report",
        "render_logistics_chat_report",
    ]
    assert {run for _, run in seen} == {RUN}


def test_채팅_쓰기와_그_앞_조회가_같은_실행을_쓴다(monkeypatch):
    seen: list[tuple[str, str]] = []

    @contextmanager
    def 연결():
        yield object()

    def 취소(conn: Any, expense_id: str, change: Any) -> dict:
        seen.append(("cancel_accrued_expense", change.sim_run_id))
        return {"expense_id": expense_id}

    def 판매(request: Any) -> Any:
        seen.append(("run_sales", request.sim_run_id))
        return SimpleNamespace(end_code="SL1", reason="ok")

    partner = SimpleNamespace(partner_id="P-1", partner_name="거래처")
    monkeypatch.setattr(ask_domain_actions.core_db, "connection", 연결)
    monkeypatch.setattr(ask_domain_actions, "cancel_accrued_expense", 취소)
    monkeypatch.setattr(ask_domain_actions, "run_sales", 판매)
    monkeypatch.setattr(
        ask_domain_actions,
        "get_console_partners",
        _capture(seen, "get_console_partners", SimpleNamespace(rows=[partner])),
    )
    monkeypatch.setattr(ask_domain_actions, "dump", lambda value: {})

    for intent in (
        _domain("FINANCE_EXPENSE_CANCEL", expense_id="EXP-1"),
        _domain(
            "SALES_PROPOSAL_CREATE",
            partner_ref="P-1",
            business_mode="SPOT_SALES",
            requested_quantity_kg="10",
        ),
    ):
        ask_domain_actions.run_domain_action(
            intent,
            as_of=AS_OF_A,
            policy_version="v1.3",
            request_id="REQ-1",
            sim_run_id=RUN,
            actor="tester",
        )

    assert [name for name, _ in seen] == [
        "cancel_accrued_expense",
        "get_console_partners",
        "run_sales",
    ]
    assert {run for _, run in seen} == {RUN}


def test_채팅_매입_실행이_요청의_실행을_쓴다(monkeypatch):
    seen: list[str | None] = []

    def 매입(request: Any) -> Any:
        seen.append(request.sim_run_id)
        return SimpleNamespace()

    monkeypatch.setattr(ask, "run_procurement", 매입)
    ask.execute(
        AskExecuteRequest(
            intent=Intent(action="PROCUREMENT_RUN", agents=[], item="배추", confidence="HIGH"),
            as_of=AS_OF_A,
            policy_version="v1.3",
            sim_run_id=RUN,
        )
    )
    assert seen == [RUN]


# ── ⑦ 서비스는 메우지 않는다 ────────────────────────────────────────────


def test_서비스는_실행_ID_없는_요청을_멈춘다(recorded):
    seen: list[AgentRequest] = []
    registry_wiring.register("finance", _spy_port(seen, {"available_cash": 1}))

    with pytest.raises(ValueError, match="sim_run_id"):
        ask.execute(
            AskExecuteRequest(
                intent=Intent(action="STATUS_QUERY", agents=["finance"], confidence="HIGH"),
                as_of=AS_OF_A,
                policy_version="v1.3",
            )
        )
    assert seen == [] and recorded == []
