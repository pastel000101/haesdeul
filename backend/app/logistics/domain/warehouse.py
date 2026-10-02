"""배치의 값 검사 — Pallet 자리 수 · 입력 문자열 · 수량 · 같은 배치 재실행.

DB 를 만지지 않는다.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

from app.logistics.schemas.warehouse import InvalidPlacementRequest, PlacementConflict

# ── 순수 도우미 ─────────────────────────────────────────────────────────


def required_pallet_count(*, quantity_kg: Decimal, kg_per_pallet: Decimal) -> int:
    """이 수량을 담는 데 필요한 자리 수. 순수 계산이다.

    ```text
    ceil(quantity_kg / kg_per_pallet)
    ```

    올림이다 — 350kg 짜리 Pallet 에 400kg 을 담으려면 두 자리가 필요하다.
    반올림하면 마지막 자투리가 갈 곳을 잃는다.
    """
    수량 = warehouse_quantity(quantity_kg, 칸="quantity_kg")
    단위 = warehouse_quantity(kg_per_pallet, 칸="kg_per_pallet")
    return math.ceil(수량 / 단위)


def warehouse_text(값: Any, *, 칸: str) -> str:
    if not isinstance(값, str) or not 값.strip():
        raise InvalidPlacementRequest(f"{칸} 가 비었다: {값!r}")
    return 값


def warehouse_quantity(값: Any, *, 칸: str) -> Decimal:
    """수량을 `Decimal` 로 좁힌다. float 도 비유한값도 받지 않는다.

    `ledger.ledger_quantity` · `outbound.outbound_quantity` 와 같은 규율이다.
    """
    if isinstance(값, bool) or not isinstance(값, Decimal):
        raise InvalidPlacementRequest(
            f"{칸} 은 Decimal 이어야 한다 (받은 것: {값!r} · {type(값).__name__})."
        )
    if not 값.is_finite():
        raise InvalidPlacementRequest(f"{칸} 이 유한한 수가 아니다: {값!r}")
    if 값 <= 0:
        raise InvalidPlacementRequest(f"{칸} 은 0보다 커야 한다 (받은 것: {값})")
    return 값


def assert_same_placement(
    기존: Any,
    *,
    pallet_id: str,
    lot_id: str,
    location_id: str,
    packaging_spec_id: str | None,
) -> None:
    """재실행이 같은 사실인지 본다. 다르면 덮지 않고 멈춘다."""
    이름 = ("lot_id", "current_location_id", "packaging_spec_id")
    있는값 = (
        기존["lot_id"],
        기존["current_location_id"],
        기존["packaging_spec_id"],
    )
    온값 = (lot_id, location_id, packaging_spec_id)
    다름 = [(칸, 있, 온) for 칸, 있, 온 in zip(이름, 있는값, 온값, strict=True) if 있 != 온]
    if 다름:
        상세 = " · ".join(f"{칸}: 기존 {있!r} ≠ 요청 {온!r}" for 칸, 있, 온 in 다름)
        raise PlacementConflict(
            f"같은 pallet_id 에 다른 배치가 이미 있다 (pallet_id={pallet_id!r}): {상세}."
            " 🔴 조용히 덮어쓰지 않는다 — 자리를 옮기려면 move_pallet 을 쓴다."
        )
