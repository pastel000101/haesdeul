"""부르는 쪽 연결로 재고 원장을 재생한다 — 개장 · 전이 · 마감 · 취소가 같은 트랜잭션 눈으로 본다."""

from datetime import date
from typing import Any

from app.finance.domain.inventory import inventory_snapshot_from_ledger_rows
from app.finance.repository.inventory import select_inventory_ledger
from app.finance.schemas.inventory import InventorySnapshot


def load_inventory_snapshot_as_of(
    conn: Any,
    *,
    sim_run_id: str,
    as_of: date,
) -> InventorySnapshot:
    """Inventory Ledger를 ``as_of``까지 재생해 재무 재고가치를 계산한다.

    두 금액은 서로 다른 Finance 표현이지만 현재 저장 계약의 수량·원가 근거는 같다.
    회계 재고원가는 남아 있는 취득원가이고, 운영 재고가치는 운영 시점의 Lot 잔량에
    취득원가를 적용한 값이다. 수량 정본은 이동 원장, 역사 원가는 입고 때 확정되어
    production에서 재평가되지 않는 Lot 원가다. 현재 잔량과 현재 상태값은 과거 계산에
    사용하지 않는다.

    부르는 쪽(개장 · 전이 · 마감 · 취소)의 연결로 읽는다 — 그 트랜잭션이 막 적은 재고 이동까지
    같은 눈으로 본다.
    """
    return inventory_snapshot_from_ledger_rows(
        select_inventory_ledger(conn, sim_run_id=sim_run_id, as_of=as_of)
    )
