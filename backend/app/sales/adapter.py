"""Sales AgentRequest adapter.

Master가 라우팅과 도메인 간 orchestration을 소유한다. 이 adapter는 Master envelope을
Sales proposal core로 옮기고, typed Sales 결과를 AgentReply로 되돌리는 경계다.

여기서는 번역만 한다 — mode 분기, 조회 결과를 STATUS_QUERY 회신으로 싣기, 마스터만 받는
실행 메타데이터(`ExecutionMetadata`). 판매 후보 생성의 실행 · 이력 저장은
`service/proposal_generation.py`(운영 화면 `POST /sales/console-proposal` 도 같은 함수를
부른다), 회신 조립은 `domain/proposal_reply.py`, 진행 상황 조회는 `readmodel/status.py`,
사람이 읽는 사실은 `domain/status_facts.py` 다. 이 파일을 import 하는 곳은 마스터 등록소
조립(`master/registry/bootstrap.py`) 하나다.
"""

from __future__ import annotations

from uuid import uuid4

from app.contracts.envelope import AgentReply, AgentRequest, ExecutionMetadata
from app.sales.domain.proposal_reply import AGENT_NAME
from app.sales.domain.status_facts import status_facts
from app.sales.llm.runtime import load_settings
from app.sales.readmodel.status import read_sales_status
from app.sales.schemas.proposal import SalesProposalReply
from app.sales.schemas.runs import SalesAgentRunResponse
from app.sales.service.proposal_generation import generate_sales_proposal


def sales_port(request: AgentRequest) -> tuple[AgentReply, ExecutionMetadata]:
    """Master가 호출하는 Sales port."""
    if request.mode == "GENERATE_SALES_PROPOSAL":
        return _generate(request)
    if request.mode == "STATUS_QUERY":
        return _status_query(request)
    return _not_implemented(request)


def _generate(request: AgentRequest) -> tuple[AgentReply, ExecutionMetadata]:
    """판매 후보 생성. 실행과 이력 저장은 service 가 한다 — 여기서는 메타데이터만 붙인다.

    계약 오류로 끝나 이력을 쓰지 않은 회신은 도구를 쓰지 않은 것으로 적는다.
    """
    generation = generate_sales_proposal(request)
    if generation.recorded is None:
        return generation.reply, _metadata(request, generation.run_id, tools=())
    return generation.reply, _metadata(
        request, generation.run_id, proposal=generation.recorded, tools=("run_proposal",)
    )


def _status_query(request: AgentRequest) -> tuple[AgentReply, ExecutionMetadata]:
    run_id = _run_id()
    status = read_sales_status(sim_run_id=request.context.sim_run_id, as_of=request.context.as_of)
    if status.runs is None:
        reply = AgentReply(
            request_id=request.context.request_id,
            as_of=request.context.as_of,
            agent=AGENT_NAME,
            mode=request.mode,
            run_id=run_id,
            runtime_status="RUNTIME_NOT_READY",
            business_status="skipped",
            missing_data=("sales_agent_runs",),
            reasoning="최근 판매 판단 이력을 확인할 수 없습니다.",
        )
        return reply, _metadata(request, run_id, tools=("list_sales_runs",))

    scoped = status.runs
    reply = AgentReply(
        request_id=request.context.request_id,
        as_of=request.context.as_of,
        agent=AGENT_NAME,
        mode=request.mode,
        run_id=str(scoped[0].run_id) if scoped else run_id,
        runtime_status="READY",
        business_status="ok",
        payload={
            "as_of": request.context.as_of.isoformat(),
            **status_facts(status.proposals, has_history=bool(scoped)),
        },
        reasoning=_status_reasoning(scoped),
    )
    return reply, _metadata(
        request, reply.run_id, tools=("list_sales_runs", "get_console_sales_proposals")
    )


def _status_reasoning(runs: list[SalesAgentRunResponse]) -> str:
    if not runs:
        return "이 실행에는 조회할 판매 판단 이력이 없습니다."
    return (
        f"최근 판매 판단 {len(runs)}건을 조회했습니다. "
        "안의 자세한 내용과 재무 검토 결과는 판매 화면의 금일 판매안에서 볼 수 있습니다."
    )


def _not_implemented(request: AgentRequest) -> tuple[AgentReply, ExecutionMetadata]:
    run_id = _run_id()
    reply = AgentReply(
        request_id=request.context.request_id,
        as_of=request.context.as_of,
        agent=AGENT_NAME,
        mode=request.mode,
        run_id=run_id,
        runtime_status="RUNTIME_NOT_READY",
        business_status="skipped",
        missing_data=(f"{request.mode}_translation",),
        missing_capability=(f"{request.mode} translation",),
        reasoning="요청하신 판매 기능은 아직 연결되지 않았습니다.",
    )
    return reply, _metadata(request, run_id, tools=())


def _metadata(
    request: AgentRequest,
    run_id: str,
    *,
    proposal: SalesProposalReply | None = None,
    tools: tuple[str, ...],
) -> ExecutionMetadata:
    settings = load_settings()
    llm_status = "DISABLED" if not settings.enabled else "SKIPPED_TEMPLATE"
    llm_model = settings.model
    llm_attempts = 0
    llm_fallback_used = False
    if proposal is not None:
        llm_status = proposal.llm.status
        llm_model = proposal.llm.llm_model or ""
        llm_attempts = proposal.llm.llm_attempts
        llm_fallback_used = proposal.llm.llm_fallback_used
    return ExecutionMetadata(
        run_id=run_id,
        request_id=request.context.request_id,
        agent=AGENT_NAME,
        used_tools=tools,
        tool_order=tuple(range(1, len(tools) + 1)),
        llm_status=llm_status,
        llm_model=llm_model,
        llm_attempts=llm_attempts,
        llm_fallback_used=llm_fallback_used,
    )


def _run_id() -> str:
    return str(uuid4())
