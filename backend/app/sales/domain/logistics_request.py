"""확정 판매 결과를 Logistics 예약 요청 계약으로 옮기는 Sales 소유 projection.

★ 2026-09-29 BL-013: `sales/logistics_request.py` 에서 옮겼다. 봉투 번역이 아니라 확정 결과를
  판매 ↔ 물류 계약으로 옮기는 순수 변환이라 `adapter.py` 가 아니라 domain 에 둔다 — 마스터
  (`sales_approval`)가 확정 직후 이 함수를 직접 부른다.
"""

from __future__ import annotations

from datetime import date

from app.contracts.sales_logistics import (
    SalesOutboundReservationRequest,
    reservation_id_for_sale_item,
)
from app.sales.schemas.sale_ledger import SaleWriteResult


def outbound_reservation_for_sale(
    result: SaleWriteResult,
    *,
    sim_run_id: str,
    as_of: date,
) -> SalesOutboundReservationRequest:
    """FEFO Lot 선택이나 출고 실행 없이 예약 요청만 만든다.

    🔴 **`as_of` = 가용량 판정 기준일 = 납품일. 확정일이 아니다**
       (D/D+1 신선도 절벽 · 2026-09-15).
       예약이 서는 시점은 확정일 D 이고, 재고를 세는 날은 할당과 같은 납품일이다 —
       부르는 쪽(`master/service/sales_approval.py`)이 `sale_date` 를 넘긴다.
    """

    if not isinstance(sim_run_id, str) or not sim_run_id.strip():
        raise ValueError("sim_run_id must not be blank")
    return SalesOutboundReservationRequest(
        reservation_id=reservation_id_for_sale_item(result.sale_item_id),
        sim_run_id=sim_run_id,
        sale_id=result.sale_id,
        sale_item_id=result.sale_item_id,
        item_id=result.item_id,
        quantity_kg=result.quantity_kg,
        as_of=as_of,
    )
