"""재무 에이전트 실행이력 저장 — 원장과 섞지 않는다.

한 호출 = 풀 연결 하나 · 트랜잭션 하나. 저장 실패를 어떻게 받을지는 부르는 쪽이 정한다 —
Controller(`service/agent.py`)는 회신을 ERROR 로 바꾸고, 어댑터가 직접 답한 경로
(`service/agent_replies.py` 의 `recorded_reply`)는 관측 기록만 남긴다.
"""

from __future__ import annotations

from app.contracts.envelope import AgentReply, AgentRequest, ExecutionMetadata
from app.core import db as core_db
from app.finance.repository.runs import insert_finance_execution


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
