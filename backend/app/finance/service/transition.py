"""승인 전이의 재무 쪽 — 계획(`build_finance_transition` · 조회 연결)과 기록
(`persist_finance_transition` · 마스터 트랜잭션 연결, commit 없음).

★ 2026-09-29 재구성 BL-014: `finance/transition.py` 를 판정 · 순서 · SQL 로 나눴다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from psycopg import Connection

from app.finance.domain.transition import transition_plan
from app.finance.readmodel.finance_state import load_finance_state_row
from app.finance.readmodel.policy import get_active_finance_policy
from app.finance.repository.transition import insert_payable, upsert_transition_state
from app.finance.schemas.data_port import FinanceDataNotReady
from app.finance.schemas.transition import ApprovedCommitmentFacts, FinanceTransitionPlan
from app.finance.service.inventory import load_inventory_snapshot_as_of


def build_finance_transition(
    commitment: ApprovedCommitmentFacts,
    *,
    target_state_date: date,
    purchase_ids: Mapping[int, str],
    sim_run_id: str | None = None,
) -> FinanceTransitionPlan:
    """승인 약정을 재무 변경으로 옮긴다. **읽고 계산만 한다 — DB 를 바꾸지 않는다.**

    순서: 날짜 확인 → 승인일 재무 상태 읽기(조회 연결) → 정책 읽기(조회 연결) →
    `transition_plan` 계산. 연결은 마스터가 트랜잭션을 열기 **전에** 빌렸다 돌려준다.

    :param target_state_date: 승인 결과 상태가 설 날. **마스터가 준다.**
        마스터 계약상 `commitment.as_of + 1 달력일` 이고 토·일·공휴일도 그대로다 —
        재무는 계산하지 않고 승인일보다 뒤인지만 본다.
    :param purchase_ids: 회차(`seq`) → `purchase_id` 매핑. **마스터가 만든다.**
        `payables.purchase_id` 는 `purchases` 를 참조하는 NOT NULL 컬럼이라 재무가
        지어낼 수 없다. 재무는 자기 회차의 값을 **`seq` 로 찾아 쓰기만** 한다 —
        하나뿐이라고 첫 값을 집거나 정렬해서 고르지 않는다.
    :raises FinanceDataNotReady: 재무가 지급 시점이나 금액을 확정할 수 없을 때.
    """
    if target_state_date <= commitment.as_of:
        # 재무 정합성 조건이지 일정 규칙이 아니다 — 승인일 이하로 두면 T0 행과 같은
        # 날에 두 상태가 서고, 그다음부터 어느 쪽이 그날의 사실인지 말할 수 없다.
        raise ValueError("target_state_date must be after the approval as_of")

    state = load_finance_state_row(commitment.as_of, sim_run_id=sim_run_id)
    if state["state_date"] != commitment.as_of:
        # 승인일 잔액을 다른 날 잔액으로 대신 계산하지 않는다.
        raise FinanceDataNotReady("historical_finance_position")

    policy = get_active_finance_policy()
    return transition_plan(
        commitment,
        state=state,
        purchase_payment_days=policy.purchase_payment_days,
        target_state_date=target_state_date,
        purchase_ids=purchase_ids,
    )


def persist_finance_transition(
    conn: Connection[dict[str, object]], transition: FinanceTransitionPlan
) -> dict[str, int]:
    """재무 소유 원장을 **부르는 쪽 연결로** 기록한다.

    ★ commit 도 rollback 도 하지 않는다 — 승인 트랜잭션은 부르는 쪽 것이다.
      물류 쓰기가 뒤에서 실패하면 이 쓰기도 함께 물러나야 한다.

    ★ 같은 승인을 다시 적용해도 새 의무가 생기지 않는다. `finance_states` 는 PK,
      `payables` 는 `purchase_id` UNIQUE 가 DB 에서 막는다 — 두 번째 적용은
      쓴 행 수 0 으로 돌아온다.
    """
    written = {"finance_states": 0, "payables": 0}
    newly_persisted_payables = Decimal(0)
    for payable in transition.payables:
        inserted = insert_payable(conn, payable)
        written["payables"] += inserted
        if inserted:
            # State는 계획 금액이 아니라 이 트랜잭션에서 실제로 새로 선 의무만
            # 누적한다. retry에서는 Payable INSERT가 0건이므로 다시 더하지 않는다.
            newly_persisted_payables += payable.amount_krw

    if newly_persisted_payables:
        inventory = load_inventory_snapshot_as_of(
            conn,
            sim_run_id=transition.sim_run_id,
            as_of=transition.next_state_date,
        )
        # 첫 승인은 원천 상태를 carry하고, 같은 target 축/날짜의 다음 승인은 기존
        # 일별 상태에 **새 Payable 금액만** 원자적으로 더한다. composite UNIQUE가
        # 동시 승인도 한 행으로 직렬화한다. 기존 target의 cash/AR 등은 보존한다.
        upserted = upsert_transition_state(
            conn,
            transition,
            new_payables_krw=newly_persisted_payables,
            inventory_book_value_krw=inventory.inventory_book_value_krw,
            operational_inventory_value_krw=inventory.operational_inventory_value_krw,
        )
        if upserted != 1:
            # Payable을 새로 세웠는데 source state에서 일별 상태를 만들거나 갱신하지
            # 못했다면 부분 성공으로 돌려주지 않는다. Master 트랜잭션이 전부 rollback한다.
            raise FinanceDataNotReady("historical_finance_position")
        written["finance_states"] += upserted
    return written
