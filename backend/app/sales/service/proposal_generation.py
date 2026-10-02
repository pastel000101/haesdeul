"""판매 후보 생성 실행 — 봉투 요청 하나를 받아 안을 만들고 이력에 남긴다.

마스터(`adapter.sales_port` · `GENERATE_SALES_PROPOSAL`)와 운영 화면
(`POST /sales/console-proposal`)이 이 함수 하나를 부른다.

```text
봉투 요청 → 제안 입력 (domain/proposal_input — 거래처 계약 결제일수를 읽어 채운다)
         → 판매 제안 그래프 (service/proposal.run_proposal)
         → 회신 (domain/proposal_reply — 계약을 못 지키면 오류 회신, 이력을 쓰지 않는다)
         → 이력 저장 (repository/runs — 연결 하나 · 트랜잭션 하나)
```

이력 저장은 원장과 따로다. 이 실행은 원장을 쓰지 않고, 이력만 제 연결 · 제 트랜잭션으로
남긴다. 저장이 실패하면 예외가 그대로 올라간다 — 마스터 경유면 `MasterRunner` 가 오류
회신으로 바꾸고, 화면 라우터는 받은 대로 올린다.
"""

import logging
from dataclasses import asdict, dataclass
from uuid import UUID, uuid4

from pydantic import ValidationError

from app.contracts.envelope import AgentReply, AgentRequest
from app.core import db as core_db
from app.sales.domain.proposal_input import proposal_input_data, with_partner_payment_days
from app.sales.domain.proposal_reply import (
    contract_error_reply,
    contract_problem,
    invalid_input_reply,
    proposal_reply,
)
from app.sales.readmodel.partners import get_partner_profile
from app.sales.repository.runs import save_sales_agent_run
from app.sales.schemas.proposal import SalesProposalInput, SalesProposalReply
from app.sales.service.proposal import run_proposal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProposalGeneration:
    """판매 후보 생성 한 번의 결과."""

    run_id: str
    reply: AgentReply
    #: 이력에 남긴 판매 결과. 계약 오류로 끝나 이력을 쓰지 않았으면 `None` 이다 —
    #: 마스터 쪽 실행 메타데이터(`adapter._metadata`)가 이것으로 모델 상태를 읽는다.
    recorded: SalesProposalReply | None


def generate_sales_proposal(request: AgentRequest) -> ProposalGeneration:
    """봉투 요청으로 판매 후보를 만들고, 회신을 이력에 남긴다."""
    run_id = _new_run_id()
    try:
        proposal_input = SalesProposalInput.model_validate(
            with_partner_payment_days(
                proposal_input_data(request, run_id), lookup=_partner_contract_payment_days
            )
        )
    except ValidationError as exc:
        return ProposalGeneration(
            run_id=run_id, reply=invalid_input_reply(request, run_id, exc), recorded=None
        )

    proposal = run_proposal(proposal_input)
    problem = contract_problem(proposal)
    if problem is not None:
        payload, reason = problem
        return ProposalGeneration(
            run_id=run_id,
            reply=contract_error_reply(request, run_id, payload=payload, reason=reason),
            recorded=None,
        )

    reply = proposal_reply(request, run_id, proposal)
    _record_run(request, reply)
    return ProposalGeneration(run_id=run_id, reply=reply, recorded=proposal)


def _record_run(request: AgentRequest, reply: AgentReply) -> None:
    """요청 · 회신 봉투를 이력 한 건으로 남긴다 — 빌린 연결 하나 · 트랜잭션 하나."""
    with core_db.connection() as conn, core_db.transaction(conn):
        save_sales_agent_run(
            conn,
            run_id=UUID(reply.run_id),
            cycle="SALES",
            as_of=request.context.as_of,
            snapshot_id=None,
            runtime_status=reply.runtime_status,
            request_payload=asdict(request),
            response_payload=asdict(reply),
        )


def _partner_contract_payment_days(partner_id: str) -> int | None:
    """거래처 계약 결제일수. 못 읽으면 `None` 이고 지어내지 않는다."""
    try:
        profile = get_partner_profile(partner_id=partner_id)
    except Exception:  # noqa: BLE001 - 못 읽은 계약을 기본값으로 메우지 않는다.
        logger.warning("partner contract payment days unavailable: %s", partner_id)
        return None
    if profile is None:
        return None
    return profile.sales_collection_days


def _new_run_id() -> str:
    return str(uuid4())
