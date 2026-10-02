from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.logistics.schemas.agent import (
    PurchaseAgentOutput,
    ScenarioAdjustment,
)
from app.logistics.schemas.snapshot import InTransitItem, ScheduledQuantity


def test_logistics_procurement_accepts_purchase_v04(logistics_purchase_payload):
    request = PurchaseAgentOutput.model_validate(logistics_purchase_payload)

    assert request.scenarios[0].total_qty_kg == 4500
    assert request.scenarios[0].split_plan[0].qty_kg == 4500


def test_logistics_procurement_rejects_legacy_ton_fields(logistics_purchase_payload):
    scenario = logistics_purchase_payload["scenarios"][0]
    scenario["total_quantity_ton"] = scenario.pop("total_qty_kg")

    with pytest.raises(ValidationError):
        PurchaseAgentOutput.model_validate(logistics_purchase_payload)


def test_logistics_procurement_rejects_duplicate_kg_names(logistics_purchase_payload):
    scenario = logistics_purchase_payload["scenarios"][0]
    scenario["total_quantity_kg"] = scenario.pop("total_qty_kg")
    scenario["split_plan"][0]["quantity_kg"] = scenario["split_plan"][0].pop("qty_kg")

    with pytest.raises(ValidationError):
        PurchaseAgentOutput.model_validate(logistics_purchase_payload)


def test_scenario_adjustment_allows_only_quantity_and_timing():
    """물류 Adjustment 축은 quantity/timing뿐 — amount/channel_mix 금지."""
    ScenarioAdjustment(axis="quantity", split_date=date(2026, 8, 21))
    ScenarioAdjustment(axis="timing", split_date=date(2026, 8, 21))
    for forbidden in ("amount", "channel_mix"):
        with pytest.raises(ValidationError):
            ScenarioAdjustment(axis=forbidden, split_date=date(2026, 8, 21))


# ── InTransitItem.purchase_id (읽기 계약만) ─────────────────────────────
#
# 🟡 **받을 자리는 뚫려 있지만 아직 안 켜졌다.** `purchase_id` 를 만드는 곳은
#    마스터이고, 그 값이 물류로 넘어오려면 마스터 전이 규약이 바뀌어야 한다 —
#    **후속 협의 안건**이다. 여기서 잠그는 것은 **모델의 읽기 계약**이고,
#    생산자 쪽 동작은 `test_logistics_transition.py` 가 잰다.


def test_기존_fixture_행은_purchase_id_없이도_읽힌다():
    """🔴 전역 필수로 올리면 이미 적혀 있는 행들이 통째로 파싱에 실패한다.

    그러면 물류가 그날 `RUNTIME_NOT_READY` 로 돌아선다 — 값 하나 때문에 판단 전체가
    멈추는 자리다. `extra="forbid"` 라 **없는 키는 금지지만 기본값 있는 새 필드는
    안전하다.**
    """
    옛_행 = {
        "inbound_id": "INB-OLD-1",
        "item": "배추",
        "quantity_kg": "100",
        "expected_arrival_date": "2026-01-07",
    }

    되읽은 = InTransitItem.model_validate(옛_행)

    assert 되읽은.purchase_id is None
    assert 되읽은.inbound_id == "INB-OLD-1"
    assert 되읽은.quantity_kg == Decimal(100)


def test_purchase_id_는_inbound_id_와_다른_정체성이다():
    """★ 둘을 같은 칸으로 접지 않는다 — B-1 대조의 열쇠는 여전히 `inbound_id` 다."""
    행 = InTransitItem(
        inbound_id="INB-H1-REQ-1-1-1",
        purchase_id="PUR-REQ-1-D1-S1",
        item="배추",
        quantity_kg=Decimal(300),
        expected_arrival_date=date(2026, 1, 2),
    )

    assert 행.inbound_id != 행.purchase_id
    assert 행.model_dump(mode="json")["purchase_id"] == "PUR-REQ-1-D1-S1"


def test_확정_입고_일정에는_purchase_id_칸이_없다():
    """🔴 **일정·수량 사실에는 출처를 얹지 않는다.**

    `ScheduledQuantity` 는 outbound 등 다른 일정에도 재사용된다. 어느 매입에서
    왔는지는 **운송 중인 물건의 속성**이지 일정의 속성이 아니다.
    """
    assert "purchase_id" not in ScheduledQuantity.model_fields

    with pytest.raises(ValidationError):
        ScheduledQuantity(
            date=date(2026, 1, 2),
            quantity_kg=Decimal(300),
            item="배추",
            inbound_id="INB-H1-REQ-1-1-1",
            purchase_id="PUR-REQ-1-D1-S1",
        )
