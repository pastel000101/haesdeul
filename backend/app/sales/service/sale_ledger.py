"""판매 원장 기록 — 승인된 안을 원장에 적고, 출고가 끝난 판매를 납품 완료로 표시한다.

★ 2026-09-29 BL-013: `sales/persistence.py` 의 `confirm_sale` · `mark_sale_delivered` 를 옮겼다.
  **트랜잭션은 마스터 것이다** — `sales_approval`(확정 + 물류 예약을 한 commit)과
  `outbound_flow`(출고 뒤 완료 표시)가 연 연결을 받아 쓰고, 여기서는 commit · rollback ·
  반환을 하지 않는다. 계획 · 판정 규칙은 `domain/sale_ledger.py`, SQL 은
  `repository/sale_ledger.py` 다.
"""

from typing import Any

from app.sales.domain.sale_ledger import build_sale_confirmation_plan, check_already_delivered
from app.sales.repository.sale_ledger import persist_sale, sale_order_statuses, set_sale_delivered
from app.sales.schemas.sale_ledger import (
    SalesConfirmationInput,
    SalesPersistenceConflict,
    SaleWriteResult,
)


def confirm_sale(conn: Any, request: SalesConfirmationInput) -> SaleWriteResult:
    """Sales 승인 결과를 계산하고 caller-owned connection으로 원장에 적는다."""

    return persist_sale(conn, build_sale_confirmation_plan(request))


def mark_sale_delivered(conn: Any, *, sale_id: str) -> bool:
    """Caller-owned completion hook after Logistics has shipped all sale_items.

    이번에 바꿨으면 `True`, 이미 납품 완료였으면 `False`, 그 밖은 충돌이다.
    """

    if not isinstance(sale_id, str) or not sale_id.strip():
        raise SalesPersistenceConflict("sale_id must not be blank")
    if set_sale_delivered(conn, sale_id=sale_id) == 1:
        return True
    check_already_delivered(sale_order_statuses(conn, sale_id=sale_id), sale_id=sale_id)
    return False
