"""
finance/adapter.py — 재무 에이전트 접점 (마스터 ↔ 재무)

    AgentPort = (AgentRequest) -> (AgentReply, ExecutionMetadata)

어댑터는 계산하지 않는다.
  숫자는 전부 `app.finance.domain.tools` 의 결정론 함수가 만든다. 여기가 하는 일은 번역뿐이다
  — 봉투를 풀어 도메인 함수를 부르고, 결과를 봉투에 담는다. 어댑터가 값을 만들면
  §1.2-3(LLM·중간 계층의 숫자 생성 금지)이 무너진다.

`as_of` 는 마스터가 준 것을 쓴다(§1.2-6).
  재무 Repository 가 "오늘"을 스스로 정하면 백테스트가 성립하지 않는다.

실패를 예외로 올리지 않는다.
  `MasterRunner._invoke` 가 잡아 `ERROR` 회신으로 바꾸지만, 입력이 없어서 못 내는 답은
  예외가 아니라 `RUNTIME_NOT_READY` 다. 둘은 재시도 가치가 다르다 (M-1 §5.1).

재무 확정분 근거 — 2026-08-27 재무 파트 v2.3 검토 회신 · M-1 §8.2.

이 파일에는 번역과 mode 분기만 있다 — 봉투 payload → 매입 제안 모델. 준비 경계 · 조회 ·
Controller 실행 · 회신 확정은 `service/`, 계산은 `domain/`. 마스터 파트 등록소가 부르는
Protocol 표면(전이 · 개장 · 마감)도 이 파일에 있다 — 표면은 인자를 넘기고 결과를
옮기기만 한다. 앱에서 이 파일을 import 하는 곳은 마스터 등록소 조립
(`master/registry/bootstrap.py`)과 재무 에이전트 라우트(`api/finance/agent.py`)다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from psycopg import Connection
from pydantic import ValidationError

from app.contracts.envelope import AgentReply, AgentRequest, ExecutionMetadata
from app.contracts.parts import ClosingPartOut
from app.finance.schemas.transition import ApprovedCommitmentFacts, FinanceTransitionPlan
from app.finance.service.agent_replies import (
    invalid_scenario_as_of_reply,
    invalid_scenario_input_reply,
    new_run_id,
    not_implemented_reply,
)
from app.finance.service.agent_run import controller_boundary, run_controller
from app.finance.service.closing import close_finance_day
from app.finance.service.day_open import is_day_open, open_finance_day
from app.finance.service.pre_sales_facts import answer_pre_sales_facts
from app.finance.service.status_query import answer_status_query
from app.finance.service.transition import build_finance_transition, persist_finance_transition
from app.purchase_agent.schemas.proposal import PurchaseProposal

__all__ = [
    "FinanceClosingAdapter",
    "FinanceDayOpening",
    "FinanceTransitionAdapter",
    "finance_port",
]


def finance_port(request: AgentRequest) -> tuple[AgentReply, ExecutionMetadata]:
    """마스터가 부르는 유일한 접점."""
    if request.mode == "PRE_PURCHASE":
        return _controller_pre_purchase(request)
    if request.mode == "STATUS_QUERY":
        return answer_status_query(request)
    if request.mode == "SCENARIO_VALIDATION":
        return _controller_scenario_validation(request)
    if request.mode == "SALES_VALIDATION":
        return _controller_sales_validation(request)
    if request.mode == "PRE_SALES_FACTS":
        return answer_pre_sales_facts(request)
    return not_implemented_reply(request)


def _controller_pre_purchase(request: AgentRequest) -> tuple[AgentReply, ExecutionMetadata]:
    context, not_ready = controller_boundary(request)
    if not_ready is not None:
        return not_ready
    assert context is not None
    return run_controller(request, context)


def _controller_scenario_validation(request: AgentRequest) -> tuple[AgentReply, ExecutionMetadata]:
    run_id = new_run_id(request)
    try:
        _purchase_proposal(request.payload)
    except ValidationError as exc:
        return invalid_scenario_input_reply(request, run_id, exc)
    proposal = _purchase_proposal(request.payload)
    if proposal.meta.as_of != request.context.as_of:
        return invalid_scenario_as_of_reply(request, run_id, proposal.meta.as_of)
    context, not_ready = controller_boundary(request)
    if not_ready is not None:
        return not_ready
    assert context is not None
    return run_controller(request, context)


def _controller_sales_validation(request: AgentRequest) -> tuple[AgentReply, ExecutionMetadata]:
    """판매 제안 재무 검증. 매입 시나리오 검증 경로를 재사용하지 않는다.

    매입처럼 `PurchaseProposal` 로 payload 를 미리 검증하지 않는다. 판매 payload 의 필수
    항목이 무엇인지는 재무 판매 Capability 가 소유하고, 빠진 것은 `ERROR` 가 아니라
    `INPUT_INCOMPLETE`(→ `skipped`) 로 나간다 — 제안이 미완성인 것은 재무 고장이 아니다.
    """
    context, not_ready = controller_boundary(request)
    if not_ready is not None:
        return not_ready
    assert context is not None
    return run_controller(request, context)


def _purchase_proposal(payload: Mapping[str, Any]) -> PurchaseProposal:
    """Envelope 전용 키를 버린 뒤 실제 Purchase 계약을 검증한다."""
    fields = PurchaseProposal.model_fields
    return PurchaseProposal.model_validate(
        {key: value for key, value in payload.items() if key in fields}
    )


# ---------------------------------------------------------------------------
# 마스터 파트 등록소가 부르는 Protocol 표면 — 인자를 넘기고 결과를 옮기기만 한다
# ---------------------------------------------------------------------------


class FinanceTransitionAdapter:
    """마스터 전이 Protocol 이 부르는 재무 쪽 얇은 입구.

    여기에는 업무가 없다. 계산은 `build_finance_transition`, 쓰기는
    `persist_finance_transition` 이 한다(`service/transition.py`). 이 클래스가 하는 일은
    마스터가 쓰는 호출 모양에 이름을 맞춰 주는 것뿐이다 — 얇게 두어야 계약이 바뀔 때 고칠
    자리가 한 곳으로 남는다.

    마스터를 import 하지 않는다. Protocol 은 구조적 타이핑이라 상속이 필요 없고, 재무가 닿아도
    되는 마스터 표면은 공유 계약뿐이다.

    연결을 열지 않고 commit·rollback 도 하지 않는다. 승인 트랜잭션은 마스터 것이다.

    실행축은 생성 때 받는다. `ApprovedCommitmentFacts` 에는 `sim_run_id` 가 없다 — 그것은
    승인의 사실이 아니라 실행의 사실이고, 마스터가 이미 들고 있다
    (`master/registry/sim_run_binding.py` 의 `SimRunBound`). 안 주면 "지금 축" 을 묻는다.
    """

    def __init__(self, *, sim_run_id: str | None = None) -> None:
        self.sim_run_id = sim_run_id

    def build(
        self,
        commitment: ApprovedCommitmentFacts,
        *,
        target_state_date: date,
        purchase_ids: Mapping[int, str],
    ) -> FinanceTransitionPlan:
        return build_finance_transition(
            commitment,
            target_state_date=target_state_date,
            purchase_ids=purchase_ids,
            sim_run_id=self.sim_run_id,
        )

    def persist(
        self, conn: Connection[dict[str, object]], row: FinanceTransitionPlan
    ) -> dict[str, int]:
        return persist_finance_transition(conn, row)


class FinanceDayOpening:
    """Open an exact calendar-day Finance state using the caller's connection only.

    어느 실행을 여는지는 부르는 쪽이 안다. `v_current_finance_state` 전체에 대고 "지금 축이
    하나뿐인가" 를 물어 축을 고르면, 실행이 하나일 때는 같은 답이지만 번인과 새 걷기가
    공존하는 순간 늘 "둘" 이라고 답한다 — 실측으로 `SIM-BURNIN-202512` 와
    `SIM-WALK-202601-LOAN` 이 함께 서자 새 걷기의 첫 개장이 `finance_runtime_axis_ambiguous`
    로 막혔다.

    그래서 실행은 생성 때 받는다. 마스터는 이미 그 값을 들고 있고
    (`master/registry/sim_run_binding.py` 의 `SimRunBound`), 재무는 받은 실행만 본다.
    `financing_mode` 는 재무가 정한다 — 마스터가 고르지 않는다.

    이 클래스는 마스터 개장 Protocol 의 표면이다. 판단 순서는 `service/day_open.py` 의
    `is_day_open` · `open_finance_day`, SQL 은 받은 연결로 실행하는 함수들이 한다.
    """

    def __init__(self, *, sim_run_id: str | None = None) -> None:
        self.sim_run_id = sim_run_id

    def is_open(self, conn: Any, *, as_of: date) -> bool:
        """Return whether the active runtime axis has a state exactly on ``as_of``."""
        return is_day_open(conn, sim_run_id=self.sim_run_id, as_of=as_of)

    def open_day(self, conn: Any, *, as_of: date, carry_from: date) -> None:
        """Carry the exact ``carry_from`` state to ``as_of`` idempotently."""
        open_finance_day(
            conn, sim_run_id=self.sim_run_id, as_of=as_of, carry_from=carry_from
        )


class FinanceClosingAdapter:
    """Translate the Finance close result into Master's structural closing result."""

    def close(self, conn: Any, *, as_of: date, sim_run_id: str) -> ClosingPartOut:
        result = close_finance_day(as_of=as_of, sim_run_id=sim_run_id, conn=conn)
        return ClosingPartOut(
            part=result.part,
            status=result.status,
            reason=result.reason,
            closed=result.closed,
            created=result.created,
        )
