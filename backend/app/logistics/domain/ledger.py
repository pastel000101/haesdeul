"""원장 쓰기의 판정 — 요청 수량 · Line 합계 · 같은 move_id 의 사실 대조 · 반영 뒤 잔량 한도.

DB 를 만지지 않는다. 쓰기 순서는 `service/ledger.py`, SQL 은 `repository/ledger.py` 에 있다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from app.logistics.schemas.ledger import (
    IDENTITY_COLUMNS,
    LINE_IDENTITY_COLUMNS,
    InvalidMoveQuantity,
    MoveIdConflict,
    MoveLine,
    MoveLineTotalMismatch,
    OriginalQuantityExceeded,
    RemainingQuantityInsufficient,
    UnsupportedMoveType,
)


def ledger_quantity(value: object, *, label: str) -> Decimal:
    """수량을 정규화한다.

    ```text
    Decimal          그대로 쓴다
    int              정확한 값이라 Decimal 로 옮긴다
    float            거절 — 이진 오차가 원장에 영원히 남는다
    non-finite       거절 — NaN · sNaN · Infinity · -Infinity
    0 이하           거절
    ```

    `Decimal(float)` 은 0.1 이 갖고 있는 이진 오차를 그대로 들여와 수량에 안 보이는
    꼬리를 남긴다 (`transition.build_next_inventory` 가 같은 이유로
    `Decimal(str(x))` 를 쓴다). 원장은 그 꼬리가 영원히 남는 자리라 아예 막는다.

    유한성 검사가 부호 검사보다 먼저다. 순서를 바꾸면 `Decimal("NaN") <= 0` 이
    `decimal.InvalidOperation` 을 올려 우리 예외가 아닌 것이 밖으로 샌다.
    `sNaN` 은 더해 보기만 해도 신호를 낸다. `is_finite()` 는 신호를 내지 않는
    술어라 여기서 안전하게 가를 수 있다.
    """
    if isinstance(value, bool):
        raise InvalidMoveQuantity(f"{label} 은 boolean 이 될 수 없다")
    if isinstance(value, Decimal):
        quantity = value
    elif isinstance(value, int):
        quantity = Decimal(value)
    else:
        raise InvalidMoveQuantity(
            f"{label} 은 Decimal 또는 int 여야 한다 (받은 것: {type(value).__name__})."
            " float 은 이진 오차를 원장에 남기므로 받지 않는다."
        )
    if not quantity.is_finite():
        raise InvalidMoveQuantity(
            f"{label} 은 유한한 수여야 한다 (받은 것: {quantity!s})."
            " NaN · sNaN · Infinity 는 수량이 아니다 — DB 에 닿기 전에 막는다."
        )
    if quantity <= 0:
        raise InvalidMoveQuantity(f"{label} 은 0보다 커야 한다 (받은 것: {quantity})")
    return quantity


def validated_move(
    *,
    move_id: str,
    move_type: str,
    quantity_kg: Decimal,
    lines: Sequence[MoveLine],
    allowed_move_types: frozenset[str],
) -> tuple[Decimal, list[Decimal]]:
    """원장에 적기 전에 요청 자체를 검사한다. DB 를 만지지 않는다.

    ```text
    Move Type 이 이 진입점의 것인가     UnsupportedMoveType
    수량 · Line 수량이 원장 수량인가     InvalidMoveQuantity
    Line 합계 == Header 수량            MoveLineTotalMismatch
    ```

    원장 쓰기(`service/ledger._record_move`)의 ① 단계다 — 이 검사를 지나야
    잠금·조회·쓰기로 간다.

    :returns: (Header 수량, Line 수량들) — 둘 다 `ledger_quantity` 로 좁힌 값.
    """
    if move_type not in allowed_move_types:
        raise UnsupportedMoveType(
            f"이 진입점이 받는 Move Type 이 아니다 (받은 것: {move_type!r},"
            f" 허용: {sorted(allowed_move_types)})."
            " DISPOSE 는 폐기 확정 전용 진입점으로만 들어오고, ADJUST 는 아직"
            " 업무 규칙이 정해지지 않았다."
        )
    quantity = ledger_quantity(quantity_kg, label="quantity_kg")
    line_quantities = [
        ledger_quantity(line.quantity_kg, label="lines[].quantity_kg") for line in lines
    ]
    if line_quantities and sum(line_quantities, start=Decimal(0)) != quantity:
        raise MoveLineTotalMismatch(
            f"Move Line 합계({sum(line_quantities, start=Decimal(0))})가"
            f" Header 수량({quantity})과 다르다 (move_id={move_id})."
            " v_move_line_integrity 가 사후에 잡을 상태를 만들지 않는다."
        )
    return quantity, line_quantities


def assert_same_move_facts(
    *, move_id: str, existing: Mapping[str, Any], requested: Mapping[str, Any]
) -> None:
    """같은 id 의 두 사실을 대조한다. 다르면 어느 쪽도 고르지 않고 멈춘다."""
    differing = [
        f"{name}: 기존={existing[name]!r} 요청={requested[name]!r}"
        for name in IDENTITY_COLUMNS
        if existing[name] != requested[name]
    ]
    if differing:
        raise MoveIdConflict(
            f"같은 move_id 가 다른 사실로 이미 있다 (move_id={move_id}). "
            + " / ".join(differing)
            + ". 덮어쓰지 않는다 — 이전 사실이 에러 없이 사라지면 없었던 것과"
            " 구별되지 않는다."
        )


def assert_same_move_lines(
    *,
    move_id: str,
    existing: Sequence[tuple[Any, ...]],
    requested: Sequence[tuple[Any, ...]],
) -> None:
    """같은 id 의 두 Line 묶음을 대조한다. 순서는 사실이 아니고 개수는 사실이다.

    ```text
    P1 10kg · P2 10kg   ↔   P2 10kg · P1 10kg      같다   (순서만 다르다)
    P1 10kg · P1 10kg   ↔   P1 10kg                다르다 (개수가 다르다)
    Line 0건            ↔   Line 1건               다르다
    ```

    multiset 으로 본다. 집합으로 보면 중복이 뭉개져 "P1 두 판" 과 "P1 한 판" 이
    같아지고, 순서까지 보면 같은 물건을 다른 차례로 적은 정상 재시도가 Conflict 가 된다.

    `Decimal("10")` 과 `Decimal("10.000000")` 은 같은 수량이다 — Python 은 값이 같은
    Decimal 의 해시를 같게 보장하므로 `Counter` 대조가 DB 왕복(numeric(18,6))을 넘어
    성립한다. 여기가 흔들리면 정상 재시도가 Conflict 로 뒤집힌다.
    """
    existing_counts = Counter(existing)
    requested_counts = Counter(requested)
    if existing_counts == requested_counts:
        return

    only_existing = existing_counts - requested_counts
    only_requested = requested_counts - existing_counts
    # `key=repr` 로 정렬한다 — Line 튜플에는 `None` 과 `Decimal` 이 섞여 있어
    # 자연 정렬은 TypeError 를 낸다. 메시지 순서를 고정하려다 진단이 터지면 안 된다.
    detail = [f"Line 수: 기존={len(existing)} 요청={len(requested)}"]
    detail += [
        f"기존에만 {count}건: {line!r}" for line, count in sorted(only_existing.items(), key=repr)
    ]
    detail += [
        f"요청에만 {count}건: {line!r}" for line, count in sorted(only_requested.items(), key=repr)
    ]
    raise MoveIdConflict(
        f"같은 move_id 가 다른 Move Line 으로 이미 있다 (move_id={move_id}). "
        + " / ".join(detail)
        + f". 대조 칸은 {', '.join(LINE_IDENTITY_COLUMNS)} 이고 순서는 보지 않는다."
        " 덮어쓰지 않는다 — 어느 Pallet 에서 나갔는지가 조용히 바뀌면"
        " 재고 실사에서 되짚을 근거가 사라진다."
    )


def next_remaining_qty(
    *,
    move_type: str,
    quantity: Decimal,
    current_remaining: Decimal,
    original: Decimal,
    lot_id: str,
    move_id: str,
) -> Decimal:
    """반영 후 잔량. 쓰기 전에 계산하고 검사한다.

    DB CHECK 둘(`remaining >= 0` · `remaining <= original`)을 우회하지 않는다.
    같은 규칙을 앞에서 한 번 더 볼 뿐이고, 그래야 실패해도 트랜잭션이 살아 있다.
    """
    # `DISPOSE` 도 잔량을 줄이는 방향이다 — OUT 과 같은 규칙을 쓴다.
    # 두 방향을 한 자리에서 보게 두어야 "폐기만 다르게 센다" 가 생기지 않는다.
    if move_type in ("OUT", "DISPOSE"):
        if quantity > current_remaining:
            raise RemainingQuantityInsufficient(
                f"{move_type} 수량({quantity})이 현재 잔량({current_remaining})보다 크다"
                f" (lot_id={lot_id}, move_id={move_id}). Move 를 남기지 않는다."
            )
        return current_remaining - quantity

    next_remaining = current_remaining + quantity
    if next_remaining > original:
        raise OriginalQuantityExceeded(
            f"IN 을 반영하면 잔량({next_remaining})이 Lot 최초 수량({original})을 넘는다"
            f" (lot_id={lot_id}, move_id={move_id}). Move 를 남기지 않는다 —"
            " 최초 수량을 늘리는 것은 원장이 아니라 입고 단계가 할 일이다."
        )
    return next_remaining
