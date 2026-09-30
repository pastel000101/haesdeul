"""사용자 판매 후보가 Master 이력과 Today Proposals 승인 키로 이어지는지 잠근다."""

from datetime import date
from pathlib import Path
from uuid import UUID

from app.contracts.envelope import AgentReply, AgentRequest, ExecutionMetadata
from app.master.readmodel import approvals
from app.master.registry import wiring as registry_wiring
from app.master.schemas.day_gate import DayGate
from app.master.schemas.inputs import SourcedInput
from app.master.schemas.sales import SalesRunRequest
from app.master.service import persistence as service_persistence
from app.master.service.sales import run_sales
from app.sales.readmodel.console_proposals import get_console_sales_proposals
from tests.master.logistics_pre_sales import PRE_SALES_PAYLOAD
from tests.sales.sales_fake_connection import lend

AS_OF = date(2026, 9, 16)
SIM_RUN = "SIM-USER-SALES-LINEAGE"
RUN_A = "11111111-1111-1111-1111-111111111111"
RUN_B = "22222222-2222-2222-2222-222222222222"


class _NoHolidays:
    """모든 날을 덮고 공휴일이 없는 달력 — `tests/master` conftest 의 달력 가짜와 같은 답이다."""

    def is_holiday(self, day: object) -> bool:
        return False


def _port(payload):
    def port(request: AgentRequest):
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
        return reply, ExecutionMetadata(
            run_id=reply.run_id,
            request_id=reply.request_id,
            agent=request.agent,
        )

    return port


def test_user_candidate_keeps_master_run_through_today_proposals_and_approval(monkeypatch):
    """RUN-B가 뒤에 생겨도 화면이 본 RUN-A의 id와 안 번호를 승인 입력으로 유지한다."""
    registry_wiring.reset()
    monkeypatch.setattr(
        "app.master.service.sales.check_day_gate",
        lambda as_of, **_kwargs: DayGate(
            as_of=as_of,
            gate="PASS",
            result="ALREADY_OPENED",
            last_opened_date=as_of,
        ),
    )
    #  ★ 2026-10-01 재구성 BL-022: `run_sales` 는 그날 매입 경계를 `master_agent_runs` 에서 읽는다.
    #    `tests/master` 는 conftest(`매입_경계_조회를_막는다`)가 그 표 접근 하나를 «행 없음»으로
    #    막지만 이 파일은 `tests/sales` 라 안 걸려, 조회가 실 DB 로 나가 막혔다(기준선 실패 1건).
    #    같은 자리를 같은 답으로 막는다 — 경계 판정 자체는 진짜 코드가 돈다.
    monkeypatch.setattr(
        "app.master.readmodel.procurement_boundary.list_runs", lambda **_kwargs: []
    )
    #  ★ 2026-10-01 BL-022 보완: 같은 이유로 `tests/master` conftest 의 두 문
    #    (`입력_적재를_끈다` · `공휴일_달력을_가짜로_준다`)도 여기 안 걸려, 판매 예측 적재와
    #    공휴일 달력 조회가 실 DB 쪽에서 막힌 채 삼켜지고 있었다. 이 검사는 둘을 재지 않는다 —
    #    같은 답(«적재 안 함» · «공휴일 없음»)을 준다.
    monkeypatch.setattr(
        "app.master.service.sales.load_forecast",
        lambda *_args, **_kwargs: SourcedInput(
            "forecast", None, "MISSING", "-", "테스트에서는 적재하지 않는다"
        ),
    )
    monkeypatch.setattr("app.master.service.sales.get_calendar", lambda: _NoHolidays())
    registry_wiring.register("inventory", _port(PRE_SALES_PAYLOAD))
    registry_wiring.register(
        "sales",
        _port(
            {
                "scenarios": [
                    {
                        "scenario_id": "SCN-A-1",
                        "scenario_type": "BALANCED",
                        "item": "배추",
                        "partner_id": "KIMCHI_FACTORY_001",
                        "quantity_kg": "100",
                        "unit_price_krw": "1200",
                        "reported_sales_amount_krw": "120000",
                        "delivery_date": "2026-09-17",
                        "payment_days": 30,
                        "required_validations": ["FINANCIAL_VALIDATION"],
                    }
                ],
                "situation": "판매 가능",
            }
        ),
    )
    registry_wiring.register("finance", _port({"verdict": "PASS"}))
    monkeypatch.setattr(service_persistence, "try_save_run", lambda **_kwargs: RUN_A)

    created = run_sales(
        SalesRunRequest(
            as_of=AS_OF,
            sim_run_id=SIM_RUN,
            policy_version="v1.3",
            business_mode="SPOT_SALES",
            partner_id="KIMCHI_FACTORY_001",
            item="배추",
            requested_quantity_kg="100",
            preferred_unit_price_krw="1200",
            preferred_delivery_date=date(2026, 9, 17),
            preferred_payment_days=30,
            preferred_payment_terms_type="SINGLE",
            source_ref="CONSOLE-SALES-LINEAGE-TEST",
        )
    )
    assert created.candidates, created.model_dump(mode="json")
    scenario = dict(created.candidates[0].scenario)

    def read_proposals(statement, params=None):
        if "source_order_id" in str(statement):
            return []
        return [
            {
                "request_id": created.request_id,
                "history_run_id": created.history_run_id,
                "payload": {"recommended_scenario_id": "SCN-A-1", "scenarios": [scenario]},
                "scenario": scenario,
                "finance_verdict": "PASS",
                "finance_status": "EVALUATED",
                "rule_results": [],
                "financial_summary": {},
            }
        ]

    monkeypatch.setattr("app.sales.repository.console_proposals.get_db_schema", lambda: "haetdeul")
    lend(monkeypatch, read_proposals)
    today = get_console_sales_proposals(sim_run_id=SIM_RUN, as_of=AS_OF)
    selected = today.rows[0]

    latest_was_used = False

    def latest(*_args, **_kwargs):
        nonlocal latest_was_used
        latest_was_used = True
        return {"run_id": UUID(RUN_B), "request_id": created.request_id}

    monkeypatch.setattr(approvals, "get_run_by_request_id", latest)
    monkeypatch.setattr(
        approvals,
        "get_run",
        lambda run_id: {
            "run_id": run_id,
            "request_id": created.request_id,
            "response_payload": created.model_dump(mode="json"),
        },
    )
    approval_run = approvals.run_for(created.request_id, selected.history_run_id)

    assert created.history_run_id == RUN_A
    assert selected.history_run_id == RUN_A
    assert selected.scenario_id == "SCN-A-1"
    assert approval_run["run_id"] == UUID(RUN_A)
    assert latest_was_used is False


def test_user_sales_form_uses_only_the_master_endpoint():
    source = (
        Path(__file__).parents[3]
        / "frontend"
        / "src"
        / "components"
        / "console"
        / "SalesCandidatePanel.tsx"
    ).read_text(encoding="utf-8")
    api_source = (Path(__file__).parents[3] / "frontend" / "src" / "lib" / "api.ts").read_text(
        encoding="utf-8"
    )

    assert "salesRun({" in source
    assert "console-proposal" not in source
    assert '"/master/sales/run"' in api_source
