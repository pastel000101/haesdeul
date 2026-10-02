"""판매 원장 기록 규칙 — 승인된 안을 원장 계획으로 고정하고, 납품 완료가 겹칠 때를 가른다.

여기는 검증 · 수금기일 · ID 규칙이다. SQL 은 `repository/sale_ledger.py`, 마스터가 넘긴
연결로 두 단계를 잇는 순서는 `service/sale_ledger.py` 다.
"""

from datetime import timedelta
from decimal import Decimal

from app.sales.schemas.proposal import SalesExecutionIdentity, SalesScenario
from app.sales.schemas.sale_ledger import (
    SaleItemWrite,
    SalesApprovalLine,
    SalesConfirmationInput,
    SalesPersistenceConflict,
    SaleWritePlan,
)

_ZERO_TOLERANCE = Decimal("0.1")


def build_sale_confirmation_plan(request: SalesConfirmationInput) -> SaleWritePlan:
    """승인된 Sales 시나리오를 실제 원장 write plan 으로 고정한다."""

    identity = request.execution_identity
    if not identity.run_id:
        raise SalesPersistenceConflict("sales approval execution identity is missing run_id")
    scenario = request.selected_scenario
    if scenario.scenario_id != request.selected_scenario_id:
        raise SalesPersistenceConflict("selected scenario id does not match the scenario payload")
    if scenario.partner_id is None:
        raise SalesPersistenceConflict("selected scenario is missing partner_id")
    if scenario.quantity_kg is None or scenario.unit_price_krw is None:
        raise SalesPersistenceConflict("selected scenario is missing quantity or unit price")
    if scenario.sales_amount_krw is None:
        raise SalesPersistenceConflict("selected scenario is missing sales amount")
    if scenario.payment_terms_type != "SINGLE":
        raise SalesPersistenceConflict(
            "installment sale confirmations need an authoritative schedule"
        )
    if scenario.payment_days is None:
        raise SalesPersistenceConflict("single-term sale confirmations need payment_days")
    if request.line.item_name != scenario.item:
        raise SalesPersistenceConflict("sale line item does not match the selected scenario item")
    if request.line.quantity_kg != scenario.quantity_kg:
        raise SalesPersistenceConflict("sale line quantity does not match the selected scenario")
    if request.line.unit_price_krw_per_kg != scenario.unit_price_krw:
        raise SalesPersistenceConflict("sale line unit price does not match the selected scenario")

    total_profit = _line_profit(request.line, scenario)
    sale_id = sale_id_for(identity, scenario)
    sale_item_id = sale_item_id_for(sale_id, 1)
    due_date = request.sale_date + timedelta(days=scenario.payment_days)
    line = SaleItemWrite(
        sale_item_id=sale_item_id,
        sale_id=sale_id,
        item_id="",
        grade=request.line.grade,
        quantity_kg=scenario.quantity_kg,
        unit_price_krw_per_kg=scenario.unit_price_krw,
        line_amount_krw=scenario.sales_amount_krw,
        contribution_profit_krw=total_profit,
        contribution_margin_rate=_decimal_or_none(
            request.line.contribution_margin_rate
            if request.line.contribution_margin_rate is not None
            else scenario.contribution_margin_rate
        ),
    )
    return SaleWritePlan(
        sale_id=sale_id,
        sim_run_id=request.sim_run_id,
        customer_partner_id=scenario.partner_id,
        order_date=request.order_date,
        sale_date=request.sale_date,
        collection_due_date=due_date,
        total_quantity_kg=scenario.quantity_kg,
        total_amount_krw=scenario.sales_amount_krw,
        contribution_profit_krw=total_profit,
        collection_status="OPEN",
        source_order_id=request.source_order_id,
        note=request.note,
        order_status="CONFIRMED",
        item_name=scenario.item,
        sale_item=line,
    )


def sale_id_for(identity: SalesExecutionIdentity, scenario: SalesScenario) -> str:
    if not identity.run_id:
        raise SalesPersistenceConflict("sales approval execution identity is missing run_id")
    return f"SALE-{identity.run_id}-{scenario.scenario_id}"


def sale_item_id_for(sale_id: str, seq: int) -> str:
    return f"SI-{sale_id}-{seq}"


def _line_profit(line: SalesApprovalLine, scenario: SalesScenario) -> Decimal:
    value = line.contribution_profit_krw
    if value is None:
        value = scenario.contribution_margin_krw
    if value is None:
        raise SalesPersistenceConflict("selected scenario is missing contribution profit")
    return value


def _decimal_or_none(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value


def check_already_delivered(order_statuses: list[object], *, sale_id: str) -> None:
    """완료 표시가 먹지 않은 판매가 이미 납품 완료인지 가른다. 아니면 충돌이다.

    ```text
    행이 하나가 아니다     없는 판매다          → 충돌
    DELIVERED             이미 끝났다          → 통과 (이번에 바꾼 것은 없다)
    그 밖                 완료로 갈 수 없다     → 충돌
    ```

    `service/sale_ledger.py` 의 `mark_sale_delivered` 가 되읽은 상태로 이 판정을 부른다.
    """
    if len(order_statuses) != 1:
        raise SalesPersistenceConflict(f"sale was not found: {sale_id}")
    status = order_statuses[0]
    if status == "DELIVERED":
        return
    raise SalesPersistenceConflict(f"sale cannot be marked DELIVERED from {status!r}")
