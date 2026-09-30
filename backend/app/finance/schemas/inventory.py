"""재고 원장 재생 결과 — 재무가 보는 재고 수량 · 가치.

★ 2026-09-29 재구성 BL-014: `finance/db.py` 에서 옮겼다. 재생 계산은 `domain/inventory.py`, SQL 은
  `repository/inventory.py`, 부르는 쪽 연결로 읽고 계산하는 순서는 `service/inventory.py`.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class InventorySnapshot:
    """재고 원장을 특정 날짜까지 재생한 Finance용 파생 스냅샷."""

    quantity_kg: Decimal
    inventory_book_value_krw: Decimal
    operational_inventory_value_krw: Decimal
