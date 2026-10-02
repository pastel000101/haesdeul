"""재무 에이전트 실행이력 저장 — 원장과 섞지 않는다.

한 호출 = 풀 연결 하나 · 트랜잭션 하나. 저장 실패를 어떻게 받을지는 부르는 쪽이 정한다 —
Controller(`service/agent.py`)는 회신을 ERROR 로 바꾸고, 어댑터가 직접 답한 경로
(`service/agent_replies.py` 의 `recorded_reply`)는 관측 기록만 남긴다.
"""

from __future__ import annotations

from datetime import date
from typing import cast

from app.contracts.envelope import AgentReply, AgentRequest, ExecutionMetadata
from app.core import db as core_db
from app.finance.repository.runs import insert_finance_agent_run, insert_finance_execution
from app.finance.schemas.agent import FinalVerdict, FinanceCycle, RuntimeStatus
from app.finance.schemas.runs import FinanceAgentRun


def save_finance_agent_run(
    *,
    cycle: FinanceCycle,
    as_of: date,
    snapshot_id: str | None,
    runtime_status: RuntimeStatus,
    verdict: FinalVerdict | None,
    request_payload: dict[str, object],
    response_payload: dict[str, object],
) -> FinanceAgentRun:
    """완성된 Finance Agent Request와 Response를 실행이력으로 저장한다.

    한 호출 = 연결 하나 · 트랜잭션 하나. 원장 트랜잭션과 섞지 않는다.
    """
    if response_payload.get("verdict") != verdict:
        raise ValueError("Finance run verdict metadata must match response_payload.verdict")
    with core_db.connection() as conn, core_db.transaction(conn):
        row = insert_finance_agent_run(
            conn,
            cycle=cycle,
            as_of=as_of,
            snapshot_id=snapshot_id,
            runtime_status=runtime_status,
            verdict=verdict,
            request_payload=request_payload,
            response_payload=response_payload,
        )
    return cast(FinanceAgentRun, row)


def save_finance_execution(
    *, request: AgentRequest, reply: AgentReply, metadata: ExecutionMetadata
) -> None:
    """마이그레이션이 설치된 경우 v2.2 하위 trace를 저장한다.

    저장 실패는 의도적으로 삼키지 않는다. 정상적인 Business 완료에는 해석 가능한
    run_id가 반드시 있어야 한다.

    한 호출 = 연결 하나 · 트랜잭션 하나. 원장 트랜잭션과 섞지 않는다 — 실패를 어떻게 받을지는
    부르는 쪽이 정한다(Controller 는 ERROR 회신, 어댑터 경로는 관측 기록).
    """
    with core_db.connection() as conn, core_db.transaction(conn):
        insert_finance_execution(conn, request=request, reply=reply, metadata=metadata)
