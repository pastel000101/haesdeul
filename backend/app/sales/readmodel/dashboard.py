"""판매 현황(대시보드) 조회 — 실행 하나 · 기준일 하나의 판매 · 채권 사실을 응답으로 편다.

★ 2026-09-29 BL-013: `sales/dashboard.py` 에서 응답 조립을 옮겼다. SQL 은
  `repository/dashboard.py`, 채권 상태 규칙은 `domain/receivable_history.py` 다. 종전에는 조회
  일곱 개가 조회마다 연결을 빌렸고, 지금은 한 번 빌린 조회 연결로 같은 순서로 읽는다.
"""

from datetime import date
from decimal import Decimal

from app.core import db as core_db
from app.core.text import decimal_or_zero
from app.sales.domain.receivable_history import projected_status
from app.sales.repository.dashboard import (
    load_collection_summary,
    load_item_summaries,
    load_recent_sales,
    load_sales_dashboard_meta,
    load_sales_receivables,
    load_sales_summary,
    load_today_confirmed_sales,
)
from app.sales.schemas.dashboard import (
    SalesCollectionStatusSummary,
    SalesDashboardMeta,
    SalesDashboardResponse,
    SalesDashboardSummary,
    SalesHistoryItem,
    SalesItemSummary,
    SalesReceivableItem,
    TodayConfirmedSaleItem,
)

_ZERO = Decimal(0)
_COLLECTION_LABELS = {
    "COLLECTED": "수금 완료",
    "PARTIAL": "일부 수금",
    "OPEN": "수금 예정",
}


def get_sales_dashboard(
    *, sim_run_id: str, as_of: date, recent_limit: int = 10
) -> SalesDashboardResponse:
    with core_db.read_connection() as conn:
        meta = load_sales_dashboard_meta(conn, sim_run_id=sim_run_id, as_of=as_of)
        summary = load_sales_summary(conn, sim_run_id=sim_run_id, as_of=as_of) or {}
        total_sales = decimal_or_zero(summary.get("total_sales_amount_krw"))
        profit = decimal_or_zero(summary.get("contribution_profit_krw"))

        return SalesDashboardResponse(
            meta=SalesDashboardMeta(
                sim_run_id=sim_run_id,
                as_of=as_of,
                data_type=None if meta is None else str(meta["data_type"]),
            ),
            summary=SalesDashboardSummary(
                sales_count=int(summary.get("sales_count") or 0),
                customer_count=int(summary.get("customer_count") or 0),
                total_sales_quantity_kg=decimal_or_zero(summary.get("total_sales_quantity_kg")),
                total_sales_amount_krw=total_sales,
                contribution_profit_krw=profit,
                contribution_margin_pct=_pct(profit, total_sales),
                received_amount_krw=decimal_or_zero(summary.get("received_amount_krw")),
                outstanding_receivables_krw=decimal_or_zero(summary.get("outstanding_receivables_krw")),
            ),
            collection_summary=_collection_summary(
                load_collection_summary(conn, sim_run_id=sim_run_id, as_of=as_of)
            ),
            items=_items(load_item_summaries(conn, sim_run_id=sim_run_id, as_of=as_of)),
            recent_sales=_recent_sales(
                load_recent_sales(
                    conn,
                    sim_run_id=sim_run_id, as_of=as_of, limit=recent_limit
                )
            ),
            today_confirmed_sales=_today_confirmed_sales(
                load_today_confirmed_sales(conn, sim_run_id=sim_run_id, as_of=as_of)
            ),
            receivables=_receivables(
                load_sales_receivables(conn, sim_run_id=sim_run_id, as_of=as_of),
                as_of=as_of,
            ),
        )


def _collection_summary(rows: list[dict[str, object]]) -> dict[str, SalesCollectionStatusSummary]:
    result = {
        status: SalesCollectionStatusSummary(count=0, sales_amount_krw=_ZERO)
        for status in ("COLLECTED", "PARTIAL", "OPEN")
    }
    for row in rows:
        status = str(row["collection_status"])
        result[status] = SalesCollectionStatusSummary(
            count=int(row.get("count") or 0),
            sales_amount_krw=decimal_or_zero(row.get("sales_amount_krw")),
        )
    return result


def _items(rows: list[dict[str, object]]) -> list[SalesItemSummary]:
    items = []
    for row in rows:
        amount = decimal_or_zero(row["sales_amount_krw"])
        profit = decimal_or_zero(row["contribution_profit_krw"])
        items.append(
            SalesItemSummary(
                item_id=str(row["item_id"]),
                item_name=str(row["item_name"]),
                line_count=int(row["line_count"]),
                total_quantity_kg=decimal_or_zero(row["total_quantity_kg"]),
                sales_amount_krw=amount,
                contribution_profit_krw=profit,
                contribution_margin_pct=_pct(profit, amount),
                avg_unit_price_krw_per_kg=decimal_or_zero(row["avg_unit_price_krw_per_kg"]),
            )
        )
    return items


def _recent_sales(rows: list[dict[str, object]]) -> list[SalesHistoryItem]:
    result = []
    for row in rows:
        amount = decimal_or_zero(row["total_amount_krw"])
        profit = decimal_or_zero(row["contribution_profit_krw"])
        status = str(row["collection_status"])
        result.append(
            SalesHistoryItem(
                sale_id=str(row["sale_id"]),
                order_date=row["order_date"],
                sale_date=row["sale_date"],
                customer_partner_id=str(row["customer_partner_id"]),
                partner_name=None if row.get("partner_name") is None else str(row["partner_name"]),
                total_quantity_kg=decimal_or_zero(row["total_quantity_kg"]),
                total_amount_krw=amount,
                contribution_profit_krw=profit,
                contribution_margin_pct=_pct(profit, amount),
                collection_due_date=row["collection_due_date"],
                collection_status=status,
                collection_status_label=_COLLECTION_LABELS.get(status, status),
                order_status=str(row["order_status"]),
            )
        )
    return result


def _today_confirmed_sales(rows: list[dict[str, object]]) -> list[TodayConfirmedSaleItem]:
    return [
        TodayConfirmedSaleItem(
            sale_id=str(row["sale_id"]),
            order_date=row["order_date"],
            sale_date=row["sale_date"],
            customer_partner_id=str(row["customer_partner_id"]),
            partner_name=None if row.get("partner_name") is None else str(row["partner_name"]),
            item_id=str(row["item_id"]),
            item_name=None if row.get("item_name") is None else str(row["item_name"]),
            quantity_kg=decimal_or_zero(row["quantity_kg"]),
            unit_price_krw_per_kg=decimal_or_zero(row["unit_price_krw_per_kg"]),
            line_amount_krw=decimal_or_zero(row["line_amount_krw"]),
            order_status=str(row["order_status"]),
        )
        for row in rows
    ]


def _receivables(rows: list[dict[str, object]], *, as_of: date) -> list[SalesReceivableItem]:
    result = []
    for row in rows:
        #  🔴 저장된 status 는 덮여 쓰인다. 복원한 금액에서 다시 세운다.
        status = projected_status(
            original_amount_krw=decimal_or_zero(row["original_amount_krw"]),
            received_amount_krw=decimal_or_zero(row["received_amount_krw"]),
        )
        due_date = row["due_date"]
        outstanding = decimal_or_zero(row["outstanding_amount_krw"])
        display_status = (
            "연체"
            if due_date < as_of and outstanding > 0
            else _COLLECTION_LABELS.get(status, status)
        )
        result.append(
            SalesReceivableItem(
                receivable_id=str(row["receivable_id"]),
                sale_id=str(row["sale_id"]),
                sale_date=row["sale_date"],
                customer_partner_id=str(row["customer_partner_id"]),
                partner_name=None if row.get("partner_name") is None else str(row["partner_name"]),
                issued_date=row["issued_date"],
                due_date=due_date,
                original_amount_krw=decimal_or_zero(row["original_amount_krw"]),
                received_amount_krw=decimal_or_zero(row["received_amount_krw"]),
                outstanding_amount_krw=outstanding,
                status=status,
                display_status=display_status,
                d_day=None if status == "COLLECTED" else (due_date - as_of).days,
            )
        )
    return result


def _pct(part: Decimal, whole: Decimal) -> Decimal:
    if whole == 0:
        return _ZERO
    return (part / whole * Decimal(100)).quantize(Decimal("0.01"))
