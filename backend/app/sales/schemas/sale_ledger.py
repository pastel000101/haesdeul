"""판매 원장 기록 계약 — 승인된 안을 `sales` · `sale_items` 에 적는 입력 · 계획 · 결과 · 충돌.

★ 2026-09-29 BL-013: 입력 모델 둘은 `sales/schemas.py`, 계획 · 결과 · 충돌 예외는
  `sales/persistence.py` 에서 옮겼다. 계획을 세우는 규칙은 `domain/sale_ledger.py`,
  SQL 은 `repository/sale_ledger.py`, 순서는 `service/sale_ledger.py` 다.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.sales.schemas.proposal import SalesExecutionIdentity, SalesScenario, reject_boolean


class SalesApprovalLine(BaseModel):
    """판매 승인 결과의 단일 행. Sales 원장 1건에 대응한다."""

    model_config = ConfigDict(extra="forbid")

    item_name: str = Field(min_length=1)
    quantity_kg: Decimal = Field(ge=0)
    unit_price_krw_per_kg: Decimal = Field(ge=0)
    grade: str | None = None
    contribution_profit_krw: Decimal | None = Field(default=None, ge=0)
    contribution_margin_rate: Decimal | None = Field(default=None, ge=0)

    @field_validator(
        "quantity_kg",
        "unit_price_krw_per_kg",
        "contribution_profit_krw",
        "contribution_margin_rate",
        mode="before",
    )
    @classmethod
    def reject_boolean_numbers(cls, value: object) -> object:
        return reject_boolean(value)


class SalesConfirmationInput(BaseModel):
    """Sales 승인 결과를 실제 원장 write로 옮기기 위한 입력 계약."""

    model_config = ConfigDict(extra="forbid")

    execution_identity: SalesExecutionIdentity
    selected_scenario: "SalesScenario"
    selected_scenario_id: str = Field(min_length=1)
    sim_run_id: str = Field(min_length=1)
    sale_date: date
    order_date: date
    source_order_id: str | None = Field(default=None, min_length=1)
    note: str | None = None
    line: SalesApprovalLine


class SalesPersistenceConflict(RuntimeError):
    """같은 승인 축으로 다른 사실이 들어왔다."""


@dataclass(frozen=True)
class SaleItemWrite:
    sale_item_id: str
    sale_id: str
    item_id: str
    grade: str | None
    quantity_kg: Decimal
    unit_price_krw_per_kg: Decimal
    line_amount_krw: Decimal
    contribution_profit_krw: Decimal
    contribution_margin_rate: Decimal | None


@dataclass(frozen=True)
class SaleWritePlan:
    sale_id: str
    sim_run_id: str
    customer_partner_id: str
    order_date: date
    sale_date: date
    collection_due_date: date
    total_quantity_kg: Decimal
    total_amount_krw: Decimal
    contribution_profit_krw: Decimal
    collection_status: str
    source_order_id: str | None
    note: str | None
    order_status: str
    item_name: str
    sale_item: SaleItemWrite


@dataclass(frozen=True)
class SaleWriteResult:
    sale_id: str
    sale_item_id: str
    item_id: str
    quantity_kg: Decimal
    sales_written: int
    sale_items_written: int
