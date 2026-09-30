"""판매 제안 결과를 봉투 회신(`AgentReply`)으로 — 마스터와 운영 화면이 받는 **같은 모양.**

★ 2026-09-29 BL-013: `sales/adapter.py` 의 `_generate` · `_contract_error` · `_invalid_input` 에서
  회신을 짓는 판단과 조립을 옮겼다. 순서와 이력 저장은 `service/proposal_generation.py`,
  실행 메타데이터(`ExecutionMetadata`)는 마스터만 받으므로 `adapter.py` 에 남았다.

★ **이력이 곧 봉투다.** `sales_agent_runs` 는 요청 · 회신 봉투를 그대로 저장하고, 운영 화면
  `POST /sales/console-proposal` 은 이 회신을 응답으로 돌려준다 — 그래서 회신 조립이
  어댑터에만 있으면 화면 경로가 어댑터를 불러야 했다.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from app.contracts.envelope import AgentReply, AgentRequest
from app.sales.schemas.proposal import SalesProposalReply

AGENT_NAME = "sales"


def contract_problem(proposal: SalesProposalReply) -> tuple[dict[str, Any], str] | None:
    """판매 결과가 **회신으로 낼 수 없는 모양**인가. 그렇다면 오류 payload 와 사유.

    ```text
    안을 만들었다는데 안이 없다          → scenarios
    입력이 모자라다는데 모자란 칸이 없다  → missing_data
    ```
    """
    if proposal.status == "SCENARIOS_GENERATED" and not proposal.scenarios:
        return (
            {"validation_errors": ["scenarios"]},
            "판매안을 생성했지만 표시할 수 있는 안이 없습니다. 실행 상태를 다시 확인해 주세요.",
        )
    if proposal.status == "INPUT_INCOMPLETE" and not proposal.missing_data:
        return (
            {"validation_errors": ["missing_data"]},
            "판매안을 만들기 위한 정보 확인 중 문제가 발생했습니다. 다시 시도해 주세요.",
        )
    return None


def proposal_reply(
    request: AgentRequest, run_id: str, proposal: SalesProposalReply
) -> AgentReply:
    """판매 결과의 회신. **`contract_problem` 이 `None` 인 결과만 받는다.**"""
    runtime_status = "READY"
    business_status = "ok"
    missing_data = tuple(proposal.missing_data)
    missing_capability = tuple(proposal.missing_capabilities)
    if proposal.status == "INPUT_INCOMPLETE":
        runtime_status = "RUNTIME_NOT_READY"
        business_status = "skipped"
    return AgentReply(
        request_id=request.context.request_id,
        as_of=request.context.as_of,
        agent=AGENT_NAME,
        mode=request.mode,
        run_id=run_id,
        runtime_status=runtime_status,
        business_status=business_status,
        payload=proposal_payload(proposal),
        missing_data=missing_data,
        missing_capability=missing_capability,
        additional_validation_required=bool(missing_capability),
        reasoning=_reasoning(proposal),
    )


def invalid_input_reply(request: AgentRequest, run_id: str, exc: ValidationError) -> AgentReply:
    """봉투 payload 가 판매 제안 입력이 되지 못했다. **어느 칸인지를 싣는다.**"""
    return contract_error_reply(
        request,
        run_id,
        payload={
            "validation_errors": [
                ".".join(str(part) for part in item["loc"]) or item["type"] for item in exc.errors()
            ]
        },
        reason="판매 요청 정보를 확인하지 못했습니다. 입력한 판매 조건을 다시 확인해 주세요.",
    )


def contract_error_reply(
    request: AgentRequest,
    run_id: str,
    *,
    payload: Mapping[str, Any],
    reason: str,
) -> AgentReply:
    """계약을 못 지킨 회신. **이력에 남기지 않는다** — 안이 서지 않았다."""
    return AgentReply(
        request_id=request.context.request_id,
        as_of=request.context.as_of,
        agent=AGENT_NAME,
        mode=request.mode,
        run_id=run_id,
        runtime_status="ERROR",
        business_status="skipped",
        payload=dict(payload),
        reasoning=reason,
    )


def proposal_payload(proposal: SalesProposalReply) -> Mapping[str, Any]:
    """봉투에 실을 모양. 🔴 **`by_alias=True` 다** (2026-09-11).

    ★ `SalesScenario.sales_amount_krw` 가 전선에서 `reported_sales_amount_krw` 로
      나가야 재무가 읽는다 (`REQUIRED_SALES_INPUT_FIELDS`). 그 이유는 그 칸 선언에
      적혀 있다 — **재무가 이 값을 다시 세서 맞대 보기 때문**이다.

    🔴 **마스터가 이름을 바꾸지 않는다.** 마스터는 안을 그대로 나르고, 이름의 주인은
       내는 쪽이다. 여기서 안 실으면 마스터가 번역기가 되어야 한다.
    """
    return proposal.model_dump(mode="json", by_alias=True)


def _reasoning(proposal: SalesProposalReply) -> str:
    if proposal.status == "INPUT_INCOMPLETE":
        return "판매안을 만들기 위해 필요한 정보가 부족합니다. 부족한 항목을 확인해 주세요."
    return "판매안을 준비했습니다. 각 안의 수량, 납기, 재무 검토 상태를 확인해 주세요."
