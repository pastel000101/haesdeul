"""미적용 전이 후보 조회 — 승인 행 · 원장 매입 ID 를 읽는다.

★ 2026-09-30 재구성 BL-018: `master/pending_transition_repository.py` 에서 옮겼다 —
  `approved_decisions`,
  `ledger_purchase_ids`. 종전 `fetch_all` 헬퍼처럼 함수 하나가 조회 연결(autocommit) 하나를 빌린다.
  SQL 은 `repository/pending_transitions.py`.
"""

from __future__ import annotations

from typing import Any

from app.core import db as core_db
from app.core.settings import get_db_schema
from app.master.repository.pending_transitions import (
    select_current_approvals,
    select_ledger_purchase_ids,
)


def approved_decisions(*, sim_run_id: str) -> list[dict[str, Any]]:
    """이 실행 축에서 **지금 유효한 결정이 승인인** 업무 키 전부. 오래된 날부터.

    🔴 **`DISTINCT ON` 으로 업무 키마다 최신 회차 하나만 본다.** `master_decisions`
       는 append-only 라 번복도 새 행이고 **최대 회차가 유효하다**
       (`decision.mark_current` 가 같은 규칙을 파이썬에서 쓴다). 회차를 안 좁히면
       취소된 승인이 영영 미적용으로 남아 날마다 다시 서게 된다.

    🔴 **매입 사이클만 본다.** 판매 승인은 `sales` 표로 흘러 `purchases` 에 영영
       안 앉는다 — 안 좁히면 *"승인됐는데 원장에 없다"* 가 판매 행에 늘 참이 되고,
       재시도가 판매 승인을 매일 헛돌린다. 어휘의 주인은 `decision.PROCUREMENT_CYCLE`
       이고 여기서 문자열을 다시 적지 않는다.

    🔴 **실행 축이 필수다.** 안 좁히면 남의 걷기와 번인 30일의 승인까지 같이 끌고
       와서, 오늘 걷는 실행이 남의 장부를 고치려 든다.

    ★ **축의 주인은 실행 이력 행이다** (`master_agent_runs.sim_run_id` ·
      `decision_service._sim_run_id_of` 와 같은 자리). 결정 표에는 그 칸이 없다.

    :returns: `request_id` · `decision_seq` · `as_of` · `sim_run_id` · `decided_by` 를 든 행들.
        ★ `decided_by` 는 재시도가 사람 승인과 자동 승인을 가르는 데 쓴다
        (설계 260915 안 A §4-4).
        승인이 없으면 **빈 목록** — 조회를 못 한 것과는 다르고, 그쪽은 예외로 오른다.
    """
    schema = get_db_schema()
    with core_db.read_connection() as conn:
        rows = select_current_approvals(conn, sim_run_id=sim_run_id, schema=schema)
    return [dict(row) for row in rows]


def ledger_purchase_ids(*, sim_run_id: str) -> list[str]:
    """이 실행 축의 매입 원장에 **실제로 서 있는** Header ID 전부.

    ★ **행의 유무만 묻는다.** 무슨 값이 어느 칸에 들었는지는 `ledger.py` 의 일이고,
      여기서 알아야 하는 것은 *"이 승인이 원장에 닿았나"* 하나다.

    🔴 **`settlement_status` 로 안 거른다.** `CANCELLED` 인 행도 **닿은 것**이다 —
       거르면 취소된 매입이 미적용으로 돌아와 같은 승인이 두 번 앉는다.
    """
    schema = get_db_schema()
    with core_db.read_connection() as conn:
        rows = select_ledger_purchase_ids(conn, sim_run_id=sim_run_id, schema=schema)
    return [row["purchase_id"] for row in rows]
