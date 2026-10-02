"""재고 원장 재생 — Lot 원가와 이동으로 수량 · 취득원가를 센다.

SQL 은 `repository/inventory.py`.
"""

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from app.finance.domain.values import decimal_value, row_value
from app.finance.schemas.data_port import FinanceDataNotReady
from app.finance.schemas.inventory import InventorySnapshot


def inventory_snapshot_from_ledger_rows(rows: Sequence[Any]) -> InventorySnapshot:
    """재고 이동 행을 재생해 수량과 취득원가를 센다. 못 믿을 행이면 막는다."""
    quantities: dict[str, Decimal] = {}
    costs: dict[str, Decimal] = {}
    for row in rows:
        lot_id = str(row_value(row, "lot_id", 0))
        unit_cost = decimal_value(row_value(row, "unit_cost_krw_per_kg", 1))
        prior_cost = costs.setdefault(lot_id, unit_cost)
        if prior_cost != unit_cost:
            raise FinanceDataNotReady("inventory_lot_cost_ambiguous")
        quantities.setdefault(lot_id, Decimal(0))

        move_type = row_value(row, "move_type", 2)
        if move_type is None:
            continue
        quantity = decimal_value(row_value(row, "quantity_kg", 3))
        if move_type == "IN":
            quantities[lot_id] += quantity
        elif move_type in {"OUT", "DISPOSE"}:
            quantities[lot_id] -= quantity
        else:
            raise FinanceDataNotReady(f"unsupported_inventory_move_type:{move_type}")
        if quantities[lot_id] < 0:
            raise FinanceDataNotReady(f"negative_inventory_lot_balance:{lot_id}")

    total_quantity = sum(quantities.values(), Decimal(0))
    acquisition_cost = sum(
        (quantity * costs[lot_id] for lot_id, quantity in quantities.items()),
        Decimal(0),
    )
    return InventorySnapshot(
        quantity_kg=total_quantity,
        inventory_book_value_krw=acquisition_cost,
        operational_inventory_value_krw=acquisition_cost,
    )
