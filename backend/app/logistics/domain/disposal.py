"""폐기의 판정 — 폐기 Move ID · 입력 · 같은 폐기 참조의 사실 대조. DB 를 만지지 않는다."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from app.logistics.schemas.disposal import DisposalIntegrityError, InvalidDisposalRequest


def disposal_move_id_for(*, disposal_id: str) -> str:
    """폐기 Move 의 멱등 키. 순수 계산이고 결정론이다.

    ```text
    MOVE-DISPOSE-{disposal_id}
    ```

    `lot_id` 만으로 짓지 않는다. 한 Lot 을 여러 번 나눠 폐기할 수 있어서,
    `lot_id` 기반 ID 는 두 번째 부분 폐기를 첫 번째의 재실행으로 오인한다.

    `disposal_id` 는 호출자가 준다. 저장소에 폐기 참조 표가 없어(실측:
    `inventory_count_*` 도 폐기 승인 표도 행 0) 물류가 채번 규칙을 지어내지
    않는다 — 그 값을 정하는 자리는 폐기를 승인하는 쪽이다.

    난수 · 시계 · 시퀀스를 쓰지 않는다.
    """
    if not disposal_id or not disposal_id.strip():
        raise InvalidDisposalRequest(f"disposal_id 가 비었다: {disposal_id!r}")
    return f"MOVE-DISPOSE-{disposal_id}"


def disposal_text(값: Any, *, 칸: str) -> str:
    if not isinstance(값, str) or not 값.strip():
        raise InvalidDisposalRequest(f"{칸} 가 비었다: {값!r}")
    return 값


def disposal_quantity(값: Any) -> Decimal:
    """폐기 수량을 좁힌다. float 도 비유한값도 받지 않는다.

    `ledger.ledger_quantity` · `outbound.outbound_quantity` 와 같은 규율이다 — `NaN` 은
    부등식을 조용히 통과해 한도 검사를 무력화한다.
    """
    if isinstance(값, bool) or not isinstance(값, Decimal):
        raise InvalidDisposalRequest(
            f"quantity_kg 는 Decimal 이어야 한다 (받은 것: {값!r} · {type(값).__name__})."
        )
    if not 값.is_finite():
        raise InvalidDisposalRequest(f"quantity_kg 가 유한한 수가 아니다: {값!r}")
    if 값 <= 0:
        raise InvalidDisposalRequest(f"quantity_kg 는 0보다 커야 한다 (받은 것: {값})")
    return 값


def assert_same_disposal(
    기존: Mapping[str, Any],
    *,
    disposal_id: str,
    sim_run_id: str,
    lot_id: str,
    quantity: Decimal,
    disposed_at: date,
    reason_code: str,
    note: str | None,
) -> None:
    """같은 폐기 참조(`move_id`)에 이미 적힌 사실이 이번과 같은가. 다르면 멈춘다.

    DB 를 만지지 않는다.
    """
    다른것 = {
        칸: (기존[칸], 값)
        for 칸, 값 in (
            ("sim_run_id", sim_run_id),
            ("lot_id", lot_id),
            ("move_type", "DISPOSE"),
            ("quantity_kg", quantity),
            ("moved_at", disposed_at),
            ("reason_code", reason_code),
            # 원장 멱등 판정과 같은 눈이다 — 같은 참조에 다른 설명이 붙으면
            # 그것도 다른 사실이다.
            ("note", note),
        )
        if 기존[칸] != 값
    }
    if 다른것:
        raise DisposalIntegrityError(
            f"같은 폐기 참조에 다른 사실이 있다 (disposal_id={disposal_id!r}):"
            f" {다른것!r}. 덮지 않는다 — 이미 없앤 재고를 다시 셈하지 않는다."
        )
