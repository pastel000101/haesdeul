"""FEFO 자동 할당이 이미 붙은 Lot 에 더 붙여도 되는지 — 나간 것 · 집힌 것 · 사람이 정한 것은 안
된다.

★ 2026-09-30 재구성 BL-015: `logistics/fefo_allocation.py` 에서 옮겼다(`_더_붙일_수_있는지_본다` →
  공개 이름).
"""

from __future__ import annotations

from decimal import Decimal

from app.logistics.schemas.outbound import AssignedAllocation, InvalidOutboundRequest


def check_can_extend_allocation(
    이미있는것: AssignedAllocation,
    *,
    reservation_id: str,
    남은목표: Decimal,
    가용: Decimal,
) -> None:
    """이 예약이 이미 붙여 둔 Lot 에 **더 붙여도 되는지** 가른다.

    ```text
    ALLOCATED · FEFO_AUTO_SELECTED   🟢 더 붙인다 (내리고 같은 정체성으로 다시 세운다)
    SHIPPED                          🔴 나간 사실이다. 무슨 이유로도 안 고친다
    PICKED                           🔴 창고에서 이미 집은 몫이다. 수량을 뒤에서 못 바꾼다
    사람이 정한 근거                  🔴 규칙이 사람의 판단을 덮지 않는다
    ```

    🔴 **`SHIPPED` 를 늘리면 조용히 어긋난다.** `ship_allocated_stock` 은
       `_HOLDING_ALLOCATION`(`ALLOCATED` · `PICKED`) 만 내보내므로 `SHIPPED` 행은
       다시 안 본다. 늘린 몫은 **원장 OUT 이 영영 안 나가는데** 배정량으로는 잡혀,
       예약은 다 찬 것으로 보이고 물건은 창고에 남는다.

    🔴 **사람이 정한 할당을 규칙이 안 덮는다.** `HUMAN_OVERRIDE` 나
       `FEFO_TOOL_CONFIRMED` 는 사람이 그 Lot 을 그만큼 쓰기로 한 판단이다.
       자동 경로가 그것을 내리고 다시 세우면 **왜 그 수량이었는지가 사라진다.**

    :raises InvalidOutboundRequest: 위 셋 중 하나일 때. **DML 전에 막는다.**
    """
    if 이미있는것.is_shipped:
        raise InvalidOutboundRequest(
            f"FEFO 차례인 Lot 의 할당이 이미 출고됐다"
            f" ({reservation_id!r} · {이미있는것.lot_id!r}):"
            f" 나간 것 {이미있는것.allocated_qty_kg} · 더 붙일 것 {남은목표}."
            " 나간 사실의 수량을 뒤에서 고치지 않는다 — 늘려도 원장 OUT 이 안 따라"
            " 나가고 예약만 다 찬 것으로 보인다. 남은 몫은 다른 예약으로 낸다."
        )
    if 이미있는것.status != "ALLOCATED":
        raise InvalidOutboundRequest(
            f"FEFO 차례인 Lot 의 할당이 이미 창고에서 집혔다"
            f" ({reservation_id!r} · {이미있는것.lot_id!r},"
            f" status={이미있는것.status!r}): 더 붙일 것 {남은목표}."
            " 집어 둔 수량을 뒤에서 바꾸지 않는다."
        )
    if not 이미있는것.is_auto_selected:
        raise InvalidOutboundRequest(
            f"FEFO 차례인 Lot 을 사람이 이미 정해 두었다"
            f" ({reservation_id!r} · {이미있는것.lot_id!r},"
            f" basis={이미있는것.allocation_basis!r}): 사람이 정한 {이미있는것.allocated_qty_kg}"
            f" · 규칙이 더 붙이려는 것 {min(가용, 남은목표)}."
            " 규칙이 사람의 판단을 덮지 않는다 — 사람이 다시 정해야 한다."
        )
