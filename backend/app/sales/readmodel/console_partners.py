"""Sales operations-console partner read models; aggregates are run-scoped.

★ A partner master row is run-independent — the same customer exists in every
  simulation.  What that customer *did* is not: sales, receivables and the latest
  sale date all belong to one run, and this module keeps that split explicit so a
  screen never shows run A's turnover next to run B's partner list.

★ 2026-09-29 BL-013: `sales/console_partners.py` 에서 옮겼다. SQL 은
  `repository/console_partners.py`, 채권 상태 규칙은 `domain/receivable_history.py` 다. 상세
  조회는 다섯 조회를 한 번 빌린 조회 연결로 종전과 같은 순서로 읽는다.
"""

from datetime import date
from decimal import Decimal

from app.contracts.aging import classify_receivable_aging
from app.core import db as core_db
from app.core.text import decimal_or_zero
from app.sales.domain.receivable_history import projected_status
from app.sales.repository.console_partners import (
    load_partner_basic,
    load_partner_items,
    load_partner_receivables,
    load_partner_rows,
    load_partner_sales,
    load_partner_totals,
)
from app.sales.schemas.console_partners import (
    ConsolePartnerBasic,
    ConsolePartnerDetailResponse,
    ConsolePartnerItemRow,
    ConsolePartnerReceivableRow,
    ConsolePartnerRow,
    ConsolePartnerSaleRow,
    ConsolePartnersResponse,
    ConsolePartnerSummary,
)

_ZERO = Decimal(0)


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def get_console_partners(
    *,
    sim_run_id: str,
    as_of: date,
    query: str | None = None,
    status: str | None = None,
    partner_type: str | None = None,
) -> ConsolePartnersResponse:
    """Partner list with this run's sales and receivable aggregates already joined."""
    with core_db.read_connection() as conn:
        rows = [
            ConsolePartnerRow(
                partner_id=str(raw["partner_id"]),
                partner_name=None if raw["partner_name"] is None else str(raw["partner_name"]),
                partner_type=None if raw["partner_type"] is None else str(raw["partner_type"]),
                status="ACTIVE" if raw["active"] else "INACTIVE",
                total_sales_krw=decimal_or_zero(raw["total_sales_krw"]),
                total_sales_count=int(raw["total_sales_count"]),
                receivable_balance_krw=decimal_or_zero(raw["receivable_balance_krw"]),
                overdue_balance_krw=decimal_or_zero(raw["overdue_balance_krw"]),
                latest_sale_date=raw["latest_sale_date"],
            )
            for raw in load_partner_rows(
                conn,
                sim_run_id=sim_run_id,
                as_of=as_of,
                query=query,
                status=status,
                partner_type=partner_type,
            )
        ]
        return ConsolePartnersResponse(sim_run_id=sim_run_id, as_of=as_of, rows=rows)


def get_console_partner_detail(
    *, sim_run_id: str, as_of: date, partner_id: str, recent_limit: int = 20
) -> ConsolePartnerDetailResponse | None:
    """One partner seen through one run.  Returns null when the partner is unknown.

    🔴 Aging comes from Finance's `classify_receivable_aging`.  Sales owning a second
    aging rule would let the two screens disagree about the same receivable.
    """
    with core_db.read_connection() as conn:
        basic_raw = load_partner_basic(conn, partner_id=partner_id)
        if basic_raw is None:
            return None
        totals = load_partner_totals(
            conn, sim_run_id=sim_run_id, as_of=as_of, partner_id=partner_id
        )
        total_sales = decimal_or_zero(totals["total_sales_krw"])
        profit = decimal_or_zero(totals["contribution_profit_krw"])
        summary = ConsolePartnerSummary(
            total_sales_krw=total_sales,
            sales_count=int(totals["sales_count"]),
            contribution_profit_krw=profit,
            contribution_margin_rate=None if total_sales == _ZERO else profit / total_sales,
            latest_sale_date=totals["latest_sale_date"],
        )
        receivables: list[ConsolePartnerReceivableRow] = []
        for raw in load_partner_receivables(
            conn, sim_run_id=sim_run_id, as_of=as_of, partner_id=partner_id
        ):
            outstanding = raw["outstanding_amount_krw"]
            if outstanding is None:
                raise ValueError("receivables.outstanding_amount_krw must not be null")
            amount = Decimal(str(outstanding))
            bucket, overdue = classify_receivable_aging(
                outstanding_amount_krw=amount, due_date=raw["due_date"], as_of=as_of
            )
            if bucket != "PAID":
                summary.receivable_balance_krw += amount
                if overdue:
                    summary.overdue_balance_krw += amount
            receivables.append(
                ConsolePartnerReceivableRow(
                    receivable_id=str(raw["receivable_id"]),
                    sale_id=str(raw["sale_id"]),
                    due_date=raw["due_date"],
                    original_amount_krw=decimal_or_zero(raw["original_amount_krw"]),
                    received_amount_krw=decimal_or_zero(raw["received_amount_krw"]),
                    outstanding_amount_krw=amount,
                    days_overdue=overdue,
                    aging_bucket=bucket,
                    #  🔴 저장된 status 는 덮여 쓰인다. 복원한 금액에서 다시 세운다.
                    status=projected_status(
                        original_amount_krw=decimal_or_zero(raw["original_amount_krw"]),
                        received_amount_krw=decimal_or_zero(raw["received_amount_krw"]),
                    ),
                )
            )
        recent = [
            ConsolePartnerSaleRow(
                sale_id=str(raw["sale_id"]),
                sale_date=raw["sale_date"],
                item=None if raw["item_id"] is None else str(raw["item_id"]),
                item_name=None if raw.get("item_name") is None else str(raw["item_name"]),
                quantity_kg=decimal_or_zero(raw["total_quantity_kg"]),
                unit_price_krw=_optional_decimal(raw["unit_price_krw_per_kg"]),
                sales_amount_krw=decimal_or_zero(raw["total_amount_krw"]),
                contribution_profit_krw=_optional_decimal(raw["contribution_profit_krw"]),
            )
            for raw in load_partner_sales(
                conn,
                sim_run_id=sim_run_id, as_of=as_of, partner_id=partner_id, limit=recent_limit
            )
        ]
        items = [
            ConsolePartnerItemRow(
                item=str(raw["item_id"]),
                item_name=None if raw.get("item_name") is None else str(raw["item_name"]),
                quantity_kg=decimal_or_zero(raw["quantity_kg"]),
                sales_amount_krw=decimal_or_zero(raw["sales_amount_krw"]),
                contribution_profit_krw=decimal_or_zero(raw["contribution_profit_krw"]),
            )
            for raw in load_partner_items(
                conn, sim_run_id=sim_run_id, as_of=as_of, partner_id=partner_id
            )
        ]
        return ConsolePartnerDetailResponse(
            sim_run_id=sim_run_id,
            as_of=as_of,
            basic=ConsolePartnerBasic(
                partner_id=str(basic_raw["partner_id"]),
                partner_name=None
                if basic_raw["partner_name"] is None
                else str(basic_raw["partner_name"]),
                partner_type=None
                if basic_raw["partner_type"] is None
                else str(basic_raw["partner_type"]),
                client_type=None
                if basic_raw["client_type"] is None
                else str(basic_raw["client_type"]),
                factory_region=None
                if basic_raw["factory_region"] is None
                else str(basic_raw["factory_region"]),
                sales_collection_days=basic_raw["sales_collection_days"],
                pricing_contract_type=None
                if basic_raw["pricing_contract_type"] is None
                else str(basic_raw["pricing_contract_type"]),
                status="ACTIVE" if basic_raw["active"] else "INACTIVE",
            ),
            summary=summary,
            recent_sales=recent,
            item_summary=items,
            receivables=receivables,
        )
