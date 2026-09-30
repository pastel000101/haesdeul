"""재고화의 판정 — Lot · Move ID 규칙과 Receipt · 매입 · Lot · 입고 Move 대조.

★ 2026-09-30 재구성 BL-015: `logistics/inbound_stock.py` 에서 옮겼다. DB 를 만지지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from app.logistics.schemas.inbound_stock import (
    IN_REASON_CODE,
    InvalidReceivingAxis,
    LotConflict,
    LotIntegrityError,
)
from app.logistics.schemas.inspections import InspectionOutcome
from app.logistics.schemas.purchase_detail import PurchaseDetail


def lot_id_for(*, receipt_id: str) -> str:
    """accepted Lot 의 PK. **순수 계산이고 결정론이다.**

    ```text
    LOT-{receipt_id}
    ```

    ★ `receipt_id` 가 이미 실행(`sim_run_id`)과 입고(`inbound_id`) 정체성을 담고
      있어 접두사만 얹으면 된다 (`inspection_id_for` 와 같은 결).

    🔴 난수 · 시계 · 시퀀스를 쓰지 않는다. 같은 Receipt 는 몇 번을 불러도 같은 값이다.
    """
    if not receipt_id or not receipt_id.strip():
        raise LotIntegrityError(f"lot_id 를 지을 수 없다 — receipt_id 가 비었다: {receipt_id!r}")
    return f"LOT-{receipt_id}"


def move_id_for(*, lot_id: str) -> str:
    """입고 Move 의 멱등 키. **Lot 에 뿌리를 둔다.**

    ★ `record_inventory_move` 가 `move_id` 로 이미 멱등하다 — 같은 키가 다시 오면
      사실을 대조하고 `applied=False` 를 돌려준다. 그 장치를 그대로 쓴다.
    """
    if not lot_id or not lot_id.strip():
        raise LotIntegrityError(f"move_id 를 지을 수 없다 — lot_id 가 비었다: {lot_id!r}")
    return f"MOVE-IN-{lot_id}"


def assert_same_purchase_reference(
    receipt: Mapping[str, Any], purchase_detail: PurchaseDetail, *, receipt_id: str
) -> None:
    """Receipt 가 든 매입 참조와 이번에 받은 매입 상세가 **같은 줄인가.**

    🔴 **두 출처를 섞지 않는다.** `receipt[...] or purchase_detail[...]` 같은 폴백을
       쓰면, 다른 매입 줄의 단가·등급으로 Lot 이 서고 그 값이 그대로 재고 원가가 된다.
       Receipt 의 두 칸은 **Receipt 를 만들 때 바로 그 매입 상세에서 온 값**이라
       달라질 수가 없다 — 다르면 호출자가 엉뚱한 상세를 들고 온 것이다.

    ⚠️ **NULL 도 정상으로 보지 않는다.** 정상 생성 경로(`create_arrived_receipt`)는
       둘 다 반드시 채운다. 비어 있다면 그 Receipt 는 이 파이프라인이 만든 것이 아니다.

    ★ **DML 이 나가기 전에 부른다.** 여기서 멈추면 트랜잭션이 살아 있어 바깥이
      다른 일을 이어갈 수 있다.
    """
    for 칸, 받은값 in (
        ("purchase_item_id", purchase_detail.purchase_item_id),
        ("item_id", purchase_detail.item_id),
    ):
        적힌값 = receipt[칸]
        if not 적힌값:
            raise LotIntegrityError(
                f"Receipt 에 {칸} 가 없다 (receipt_id={receipt_id!r})."
                " 정상 생성 경로는 둘 다 채운다 — 비어 있으면 이 파이프라인이 만든"
                " Receipt 가 아니다. 매입 상세 값으로 대신 채우지 않는다."
            )
        if 적힌값 != 받은값:
            raise LotIntegrityError(
                f"Receipt 의 {칸} 와 받은 매입 상세가 다르다"
                f" (receipt_id={receipt_id!r}): receipt={적힌값!r} detail={받은값!r}."
                " 다른 매입 줄의 단가·등급으로 Lot 을 세우지 않는다."
            )


def assert_same_lot(기존: Mapping[str, Any], 이번: Mapping[str, Any], *, receipt_id: str) -> None:
    """기존 Lot 이 이번에 만들려던 것과 같은 사실인가.

    ★ `Decimal` · `date` 값으로 비교한다 — 문자열이면 `100` 과 `100.000000` 이 갈려
      **정상 재실행이 Conflict 로 뒤집힌다.**
    """
    다른것 = {
        칸: (기존[칸], 이번[칸])
        for 칸 in (
            "sim_run_id",
            "purchase_item_id",
            "item_id",
            "original_qty_kg",
            "unit_cost_krw_per_kg",
            "grade",
            "received_at",
        )
        if 기존[칸] != 이번[칸]
    }
    if 다른것:
        raise LotConflict(
            f"같은 Receipt 에 다른 사실의 Lot 이 이미 있다 (receipt_id={receipt_id!r}):"
            f" {다른것!r}. 덮지도 버리지도 않는다 — 그 Lot 의 원가·등급으로 이미"
            " 판매·평가가 돌았을 수 있다."
        )


def assert_existing_move(
    적힌: Mapping[str, Any] | None,
    *,
    move_id: str,
    sim_run_id: str,
    lot_id: str,
    quantity_kg: Decimal,
    moved_at: date,
) -> None:
    """이미 재고화를 주장하는 Receipt 에 그 입고 Move 가 **실재하는가.**

    🔴 **완료 상태에서는 없는 Move 를 새로 만들지 않는다.** `record_inventory_move`
       를 무조건 부르면 Move 가 없을 때 **조용히 만들어 버린다** — 그러면 사라진
       원장 기록이 있었다는 사실조차 안 남고, 잔량도 그때 다시 올라간다.

    ★ **읽기만 한다. 원장 업무 로직을 복제하지 않는다.** 잔량 계산·수량 규칙은 여전히
      `ledger.py` 소유이고, 여기서는 *"완료라고 적힌 것이 사실인가"* 만 확인한다.

    ⚠️ 2건 이상은 `inventory_moves_pkey` 가 막으므로 DB 계약을 믿는다.

    :param 적힌: `select_in_move` 가 읽은 그 Move 의 칸들. 없으면 `None`.
    """
    if 적힌 is None:
        raise LotIntegrityError(
            f"재고화가 끝났다는 Receipt 인데 입고 Move 가 없다 (move_id={move_id!r})."
            " 여기서 새로 만들어 조용히 복구하지 않는다 — 사라진 원장 기록이 있었다는"
            " 사실조차 안 남는다."
        )
    기대 = {
        "sim_run_id": sim_run_id,
        "lot_id": lot_id,
        "move_type": "IN",
        "quantity_kg": quantity_kg,
        "moved_at": moved_at,
        "reason_code": IN_REASON_CODE,
        "sale_item_id": None,
    }
    다른것 = {칸: (적힌[칸], 기대[칸]) for 칸 in 기대 if 적힌[칸] != 기대[칸]}
    if 다른것:
        raise LotIntegrityError(
            f"기존 입고 Move 가 이번 사실과 다르다 (move_id={move_id!r}): {다른것!r}."
            " 고치지 않는다 — 그 수량으로 잔량이 이미 움직였다."
        )


def assert_receipt_matches(
    receipt: Mapping[str, Any], outcome: InspectionOutcome, *, receipt_id: str
) -> None:
    """Receipt 에 옮겨진 수량이 검수와 같은가.

    ⚠️ 검수 단계가 이미 옮겼어야 하는 값이다. 다르면 둘 중 하나가 틀린 것이고,
       그 위에서 재고를 만들면 **틀린 수량이 가용재고가 된다.**
    """
    if (
        receipt["accepted_qty_kg"] != outcome.accepted_qty_kg
        or receipt["hold_qty_kg"] != outcome.hold_qty_kg
        or receipt["rejected_qty_kg"] != outcome.reject_qty_kg
    ):
        raise LotIntegrityError(
            f"Receipt 수량이 검수와 다르다 (receipt_id={receipt_id!r}):"
            f" receipt=({receipt['accepted_qty_kg']}, {receipt['hold_qty_kg']},"
            f" {receipt['rejected_qty_kg']}) 검수={outcome!r}."
            " 어느 쪽이 맞는지 여기서 고르지 않는다."
        )


def check_receiving_axis(*, sim_run_id: str, as_of: date, usage_scope: str) -> None:
    """도착 처리 조회 축의 문자열 두 칸(`sim_run_id` · `usage_scope`)이 있는가.

    DB 를 만지지 않는다.
    """
    for axis, value in (("sim_run_id", sim_run_id), ("usage_scope", usage_scope)):
        if not value or not value.strip():
            raise InvalidReceivingAxis(
                f"도착 처리에 쓸 수 없는 {axis} 다: {value!r}"
                f" (sim_run_id={sim_run_id!r}, as_of={as_of}, usage_scope={usage_scope!r})."
                " 없는 열쇠로 물으면 0건이 돌아오고 그것은 '그날 행이 없다' 로 읽힌다 —"
                " 없는 것과 물어보지 못한 것은 다른 사실이다."
            )
