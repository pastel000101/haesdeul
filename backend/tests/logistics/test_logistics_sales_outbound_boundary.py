from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.contracts.sales_logistics import SalesOutboundReservationRequest
from app.logistics.schemas.outbound import ReservationResult
from app.logistics.service.outbound import reserve_confirmed_sale_available


def test_sales_partial_reservation_request_calls_reserve_available_stock(monkeypatch):
    """시뮬레이션 경계는 부분예약 코어로 간다. 공용 DTO 를 그대로 푼다."""
    captured = {}

    def fake_reserve_available_stock(conn, **kwargs):
        captured["conn"] = conn
        captured.update(kwargs)
        return ReservationResult(
            applied=True,
            reservation_id=kwargs["reservation_id"],
            status="RESERVED",
            required_qty_kg=kwargs["required_qty_kg"],
            # 확보량이 요구량보다 작을 수 있다는 것이 이 문의 뜻이다.
            reserved_qty_kg=Decimal(6000),
        )

    monkeypatch.setattr(
        "app.logistics.service.outbound.reserve_available_stock", fake_reserve_available_stock
    )
    conn = object()
    request = SalesOutboundReservationRequest(
        reservation_id="RSV-SI-SALE-1-1",
        sim_run_id="SIM-1",
        sale_id="SALE-1",
        sale_item_id="SI-SALE-1-1",
        item_id="ITEM-BAECHU",
        quantity_kg=Decimal(8500),
        as_of=date(2026, 9, 10),
    )

    result = reserve_confirmed_sale_available(conn, request)

    assert result.required_qty_kg == Decimal(8500)
    assert result.reserved_qty_kg == Decimal(6000)
    assert captured == {
        "conn": conn,
        "reservation_id": "RSV-SI-SALE-1-1",
        "sim_run_id": "SIM-1",
        "item_id": "ITEM-BAECHU",
        "required_qty_kg": Decimal(8500),
        "sale_id": "SALE-1",
        "as_of": date(2026, 9, 10),
    }
