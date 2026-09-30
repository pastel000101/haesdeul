"""PRE_SALES 요청 해석 — 판매가 묻는 품목 · 수량 · 납품일을 봉투 payload 에서 읽는다.

★ 2026-09-30 재구성 BL-015: `logistics/adapter.py` 에서 옮겼다(내용 그대로). 조립 순서는
  `service/pre_sales.py`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.contracts.envelope import AgentReply, AgentRequest, ExecutionMetadata
from app.logistics.domain.agent_replies import AGENT, execution_meta
from app.logistics.domain.tools import SupplyByDate, fefo_inventory_cost_basis
from app.logistics.schemas.agent import InventoryByItem, InventoryCostBasisSnapshot
from app.logistics.schemas.snapshot import InventoryLogisticsSnapshot

#: 물류 내부 어휘 → 마스터가 읽는 사실 이름. 하나가 사라지면 다른 하나도 사라진다.
DELIVERY_MISSING_NAMES: dict[str, str] = {
    "OUTBOUND_PREP_LEAD_DAYS_UNRESOLVED": "earliest_delivery_date",
    "DELIVERY_ROUTE_UNRESOLVED": "delivery_route",
}


@dataclass(frozen=True)
class SalesAsk:
    """사용자가 실제로 물은 것. **없는 칸은 `None` 이고 물류가 채우지 않는다.**"""

    item: str | None
    quantity_kg: Decimal | None
    delivery_date: date | None


def sales_ask(request: AgentRequest) -> SalesAsk:
    """`user_request` 에서 **온 것만** 읽는다 (`query_scope` 와 같은 규율).

    🔴 **수량도 날짜도 지어내지 않는다.** 없으면 그 축의 판정을 안 하고, 안 한 것을
       `READY` 로도 `FAIL` 로도 적지 않는다.
    """
    raw = request.payload.get("user_request")
    if not isinstance(raw, Mapping):
        return SalesAsk(item=None, quantity_kg=None, delivery_date=None)
    item = raw.get("item")
    return SalesAsk(
        item=item if isinstance(item, str) and item.strip() else None,
        quantity_kg=ask_quantity(raw.get("requested_quantity_kg")),
        delivery_date=ask_date(raw.get("preferred_delivery_date")),
    )


def ask_quantity(value: Any) -> Decimal | None:
    """숫자면 `Decimal`, 아니면 `None`. 🔴 **못 읽은 값을 0 으로 바꾸지 않는다.**"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float, str)):
        try:
            return Decimal(str(value))
        except (ArithmeticError, ValueError):
            return None
    return None


def ask_date(value: Any) -> date | None:
    """`date` 거나 ISO 문자열이면 날짜, 아니면 `None`. **오늘로 메우지 않는다.**"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def confirmed_inventory_cost_basis(
    snapshot: InventoryLogisticsSnapshot,
    *,
    asked: SalesAsk,
    inventory_by_item: Sequence[InventoryByItem],
    supply_rows: Sequence[SupplyByDate],
) -> InventoryCostBasisSnapshot | None:
    """확정 물량에 FEFO 로 배부된 **예상** 재고 취득원가. **없으면 `None` 이다.**

    ```text
    덮을 물량 = min(사용자가 물은 수량, 그 날짜(또는 현재)의 확정 판매가능량)
    ```

    🔴 **판매가능량을 여기서 다시 셈하지 않는다.** 위에서 이미 낸
       `inventory_by_item` · `supply_capacity_by_date` 를 그대로 읽는다 — 같은 회신
       안에서 *"팔 수 있다고 답한 양"* 과 *"원가를 배부한 양"* 이 갈리면 그 회신은
       스스로 모순된다.

    ★ 수량을 안 물었으면 확정 판매가능량 전체가 대상이다 — 판매가 사람 없는 자동
      걷기에서 그 값을 그대로 제안 수량으로 쓴다 (`proposal._confirmed_sellable_qty`).
    """
    if asked.item is None:
        return None
    if asked.delivery_date is not None:
        row = next((row for row in supply_rows if row.date == asked.delivery_date), None)
        confirmed = None if row is None else row.confirmed_sellable_quantity_kg
    else:
        entry = next((entry for entry in inventory_by_item if entry.item == asked.item), None)
        confirmed = None if entry is None else entry.available_qty_kg
    if confirmed is None:
        return None
    quantity = confirmed if asked.quantity_kg is None else min(asked.quantity_kg, confirmed)
    return fefo_inventory_cost_basis(snapshot, item=asked.item, quantity_kg=quantity)


def delivery_input_error(
    request: AgentRequest, run_id: str, tools: Sequence[str]
) -> tuple[AgentReply, ExecutionMetadata]:
    """납기 입력 조회의 **실행 실패** — `RUNTIME_NOT_READY` 가 아니다.

    `snapshot_error_reply` 와 같은 판단이다 (M-1 §5.1): 데이터 부재가 아니라 다시 부르면
    성공할 수 있는 쪽이라 마스터가 재시도할 수 있어야 한다. 예외 원문은 싣지 않는다 —
    숫자가 섞이면 `E-REASONING-NUMERIC` 에 걸린다.
    """
    reply = AgentReply(
        request_id=request.context.request_id,
        as_of=request.context.as_of,
        agent=AGENT,
        mode=request.mode,
        run_id=run_id,
        runtime_status="ERROR",
        business_status="skipped",
        payload={"failed_operation": "load_delivery_inputs"},
        reasoning=(
            "납기 판정 입력 조회가 실행 오류로 실패했다 — "
            "데이터 부재가 아니라 재시도 가치가 있는 실패다."
        ),
    )
    return reply, execution_meta(request, run_id, tools, reply)


#: 운송 계약 조회가 **실행 오류**로 끝났다는 표시. 🔴 `None` 을 안 쓴다 —
#: `None` 은 *"계약 행이 없다"* 라는 정상 사실이고 이것은 *"못 읽었다"* 다.
def query_scope(request: AgentRequest, as_of: date) -> dict[str, Any]:
    """이 회신이 **무엇을 기준으로 답했나.** 마스터가 보낸 것만 읽는다.

    ★ **추론하지 않는다.** 마스터가 ②에 싣는 것은 사용자 조건 그대로이고
      (`sales_flow._context_input`), 거기 없는 것은 물류도 모른다.
      `delivery_window_start/end` 도 `max_confirmed_sellable_quantity_kg` 도 만들지
      않는다 — 특히 뒤엣것은 판매가 **쓰지 않기로 못박은** 값이다
      (`test_delivery_date_uses_exact_logistics_vector_not_query_scope_max`).

    ★ 품목이 없으면 **칸을 만들지 않는다.** `item: None` 을 실으면 받는 쪽이
      *"품목 지정이 없었다"* 와 *"물류가 안 읽었다"* 를 구별할 수 없다 (§1.2-10).
    """
    scope: dict[str, Any] = {"as_of": as_of.isoformat()}
    user_request = request.payload.get("user_request")
    if isinstance(user_request, Mapping):
        item = user_request.get("item")
        if isinstance(item, str) and item.strip():
            scope["item"] = item
    return scope
