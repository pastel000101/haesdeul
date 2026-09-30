"""ledger.py — 재고 수량이 바뀔 때 **원장과 잔량을 한 트랜잭션에서 함께** 바꾼다.

```text
inventory_moves                  앞으로 생기는 증감의 감사 가능한 원장
inventory_lots.remaining_qty_kg  기존 Agent 가 계속 읽는 현재 잔량
```

🔴 **둘 중 하나만 바뀌는 경로를 만들지 않는 것이 이 모듈의 존재 이유다.**
   따로 바뀌면 *"왜 그 숫자인지"* 를 설명할 수 없는 잔량이 생기고, 그 순간
   `v_move_line_integrity` 도 `inventory_moves` 도 사후 검증 도구로 쓸모가 없어진다.

⚠️ **정본을 옮기지 않았다.** DB 주석(`inventory_lots.remaining_qty_kg`)은
   *"정본은 원장"* 이라 적고 있지만 `repository/current.py` 는 아직 Lot 잔량을 읽는다.
   이 모듈은 그 차이를 **뒤집지 않는다** — 앞으로의 변경이 둘 다 건드리게만 한다.
   기존 232 Move · 80 Lot 을 재계산하지도 Backfill 하지도 않는다.

★ 이번 판이 아는 Move Type 은 `IN` · `OUT` **둘뿐이다.**
  `DISPOSE` · `ADJUST` 는 DB CHECK 어휘로 남아 있지만 실행 기능을 만들지 않았다
  (`ADJUST_IN`/`ADJUST_OUT` 분할도 하지 않는다). 모르는 것을 아는 척하지 않으려고
  명시적으로 막는다 — 조용히 통과시키면 검증 안 된 경로로 원장이 자란다.

🔴 **커밋도 롤백도 하지 않고 커넥션을 새로 열지도 않는다.**
   `transition.persist_inventory` 와 같은 규율이다. 마스터가 재무 write 와 한 번에
   커밋할 수 있어야 하고, 여기서 커밋하면 **재고만 먼저 확정된 반쪽 장부**가 남는다.
   그래서 `db.fetch_all` · `db.execute_returning_one` 을 쓰지 않는다 — 그것들은
   자기 커넥션을 연다.

⚠️ **재고 원장 쓰기는 트랜잭션 수명의 advisory lock 하나로 의도적으로 직렬화한다.**
   한 바깥 트랜잭션이 여러 재고 이동을 기록할 때 생기는 자원 교차 교착
   (advisory lock ↔ row lock)을 이렇게 없앤다. MVP 의 정확성 우선 결정이며, 필요해지면
   나중에 일괄 잠금 프로토콜로 최적화할 수 있다 (`_lock_ledger_writes` 에 상세).

⚠️ **실패에는 두 종류가 있고 트랜잭션 상태가 다르다. 하나로 뭉뚱그리지 않는다.**

```text
업무 검증 실패                        DML 전에 멈춘다      트랜잭션은 계속 쓸 수 있다
  InvalidMoveQuantity                 아무것도 안 썼다
  UnsupportedMoveType
  MoveLineTotalMismatch
  MoveIdConflict
  RemainingQuantityInsufficient
  OriginalQuantityExceeded
  LotNotFound

DB 무결성 실패                        DML 중에 터진다      🔴 트랜잭션이 aborted 다
  없는 pallet_id · location_id (FK)   INSERT 가 나간 뒤다
  CHECK 위반 (remaining >= 0 등)
  UniqueViolation
```

  🔴 **DB 무결성 실패는 바깥 트랜잭션이 rollback 해야 한다.** 그 뒤로는 같은 커넥션에
     어떤 문장도 못 보낸다 (`current transaction is aborted`). 이 모듈은 그것을 잡지도
     삼키지도 않는다 — 마스터가 경계의 주인이라 되돌릴 곳도 마스터다.
  ★ 그렇다고 FK 를 미리 다 조회해 막지 않는다. 조회와 INSERT 사이는 여전히 비어 있어
    경합을 못 막고, 검사만 두 배로 늘어난다. 막을 수 있는 것(업무 규칙)을 앞에서 막고,
    DB 가 주인인 것은 DB 에 맡긴다.

★ 2026-09-30 재구성 BL-015: `logistics/ledger.py` 을 계층별로 나눴다. 이 파일에는 원장 쓰기의
  **순서**(검증 → 원장 잠금 → 기존 Move 대조 → Lot 행 잠금 → 수량 규칙 →
  INSERT · UPDATE)가 남았다. 판정은 `domain/ledger.py`, SQL 은 `repository/ledger.py`, 잠금은
  `repository/locks.py`, 모델은 `schemas/ledger.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from app.logistics.domain.ledger import (
    assert_same_move_facts,
    assert_same_move_lines,
    next_remaining_qty,
    validated_move,
)
from app.logistics.repository.ledger import (
    insert_move,
    insert_move_line,
    lock_lot_row,
    select_current_remaining,
    select_existing_move,
    select_existing_move_lines,
    update_lot_remaining,
)
from app.logistics.repository.locks import lock_ledger_writes
from app.logistics.schemas.ledger import (
    DISPOSE_MOVE_TYPES,
    SUPPORTED_MOVE_TYPES,
    LedgerResult,
    MoveLine,
    MoveType,
)


def _record_disposal_move(
    conn: Any,
    *,
    move_id: str,
    sim_run_id: str,
    lot_id: str,
    quantity_kg: Decimal,
    moved_at: date,
    reason_code: str,
    note: str | None = None,
) -> LedgerResult:
    """폐기 확정이 남기는 `DISPOSE` Move. **`disposal.confirm_disposal()` 전용이다.**

    🔴 **공개 API 가 아니다.** `__all__` 에 없고 이름도 밑줄로 시작한다 —
       폐기의 업무 진입점은 `disposal.confirm_disposal()` **하나**여야 하고,
       이 함수가 밖에서 보이면 후보 검증·예약 보호를 건너뛰는 우회로가 생긴다.

    🔴 **다른 곳에서 부르지 않는다.** 폐기 여부·수량 한도·예약 보호는 `disposal.py`
       가 판단하고, 이 함수는 그 판단이 끝난 뒤 **원장에 적는 일만** 한다.
       여기에 업무 규칙을 얹으면 원장이 다시 판단하는 자리가 되고, 두 곳이 갈린다.

    ★ **검증된 경로를 그대로 쓴다.** 잠금 → 멱등 대조 → Lot `FOR UPDATE` → 수량 검사
      → 쓰기 순서가 `record_inventory_move` 와 **같은 함수**다. 폐기만 다른 길로
      가면 그 길에서 잔량이 어긋난다.

    ⚠️ `sale_item_id` 와 `lines` 를 받지 않는다 — 폐기는 판매 건도 Pallet 배분도 없다.
    """
    return _record_move(
        conn,
        move_id=move_id,
        sim_run_id=sim_run_id,
        lot_id=lot_id,
        move_type="DISPOSE",
        quantity_kg=quantity_kg,
        moved_at=moved_at,
        reason_code=reason_code,
        sale_item_id=None,
        note=note,
        lines=(),
        allowed_move_types=DISPOSE_MOVE_TYPES,
    )


def record_inventory_move(
    conn: Any,
    *,
    move_id: str,
    sim_run_id: str,
    lot_id: str,
    move_type: MoveType,
    quantity_kg: Decimal,
    moved_at: date,
    reason_code: str,
    sale_item_id: str | None = None,
    note: str | None = None,
    lines: Sequence[MoveLine] = (),
) -> LedgerResult:
    """Move 를 남기고 Lot 잔량을 **같은 트랜잭션에서** 바꾼다.

    ```text
    ① 입력·Line 검증                   DB 를 안 만진다
    ② 원장 쓰기 advisory xact lock     재고 원장 쓰기 전체를 한 줄로 세운다
    ③ 같은 move_id 가 이미 있나
       ├ 있음  Header 대조 → Line 대조 → Lot 잔량 **읽기** → applied=False
       └ 없음  ↓
    ④ Lot 을 FOR UPDATE 로 잠근다      잔량을 바꾸는 행을 고정한다
    ⑤ 수량 규칙을 검사한다             ★ 쓰기 전에 한다 — 실패해도 Move 가 안 남는다
    ⑥ Move INSERT
    ⑦ Move Line INSERT (주어진 경우만)
    ⑧ Lot UPDATE
    ```

    🔴 **② 가 ③ 앞이고 ③ 이 ④ 앞인 것이 이 함수의 동시성 계약이다.**

    ```text
    ② 없으면   같은 move_id 를 다른 Lot 으로 보낸 둘이 서로 다른 행을 잠그고 지나쳐,
               뒤엣것이 MoveIdConflict 가 아니라 raw UniqueViolation 으로 터진다
    ③ 이 ④ 뒤면 "같은 move_id 인데 없는 Lot" 이 LotNotFound 로 나간다 —
               그것은 부재가 아니라 멱등 키 충돌이다
    ⑤ 가 ⑥ 뒤면 초과 OUT 이 "Move 는 남고 잔량은 그대로" 를 만들고,
               정리를 호출자의 rollback 에 떠넘기게 된다
    ```

    ★ **한 바깥 트랜잭션이 이 함수를 여러 번 불러도 안전하다.** ② 의 잠금은 하나뿐이고
      같은 트랜잭션 안에서 재진입하므로 두 번째 호출이 자기를 막지 않는다. 잠금이
      `move_id` 별이던 종전 판은 여기서 교착이 났다 (`lock_ledger_writes` 참조).

    🔴 **커밋·롤백하지 않고 커넥션을 새로 열지 않는다.** 인자로 받은 `conn` 만 쓴다.
       advisory lock 도 transaction-level 이라 **호출자의 커밋/롤백과 함께** 풀린다 —
       이 모듈은 unlock 을 부르지 않는다.

    ★ ③ 이 ⑤ 보다 **먼저**여야 한다. 순서를 바꾸면 이미 반영된 OUT 을 다시 보냈을 때
      (잔량이 이미 줄어 있으므로) 초과 판정이 나서, **정상 재시도가 오류가 된다.**

    ⚠️ **멱등 보장은 READ COMMITTED 를 전제로 한다** (PostgreSQL 기본값이자 이 DB 의
       설정값). REPEATABLE READ 이상에서는 두 번째 트랜잭션의 스냅샷이 advisory lock 을
       기다리기 **전에** 찍혀, 먼저 커밋된 Move 를 못 보고 INSERT 로 나아갈 수 있다.
       바깥 트랜잭션의 격리수준은 마스터가 정하므로 여기서 강제하지 않고 밝혀만 둔다.

    :param conn: 마스터/호출자가 쥔 커넥션. 이 함수는 소유하지 않는다.
    :param move_id: 원장 PK 이자 **멱등 키**. 같은 값을 두 번 보내도 잔량은 한 번만 바뀐다.
    :param lines: Pallet 단위 내역. 비어 있어도 된다 — Pallet 확정 전이 그 상태다.
        주면 합계가 `quantity_kg` 와 정확히 같아야 하고, 재실행 시 **Line 사실도**
        같아야 한다 (순서는 무관, 개수는 유의미 — `assert_same_move_lines`).
    :raises LotNotFound: 대상 Lot 이 없다. 만들지 않는다.
    :raises UnsupportedMoveType: `IN`/`OUT` 이 아니다.
    :raises MoveIdConflict: 같은 `move_id` 가 다른 Header 사실 **또는 다른 Line** 으로
        이미 있다.
    :raises InvalidMoveQuantity: 수량이 Decimal/int 가 아니거나, 유한하지 않거나, 양수가
        아니다.
    :raises RemainingQuantityInsufficient: OUT 이 현재 잔량을 넘는다.
    :raises OriginalQuantityExceeded: IN 이 Lot 최초 수량을 넘게 만든다.
    :raises MoveLineTotalMismatch: Line 합계가 Header 수량과 다르다.
    """
    return _record_move(
        conn,
        move_id=move_id,
        sim_run_id=sim_run_id,
        lot_id=lot_id,
        move_type=move_type,
        quantity_kg=quantity_kg,
        moved_at=moved_at,
        reason_code=reason_code,
        sale_item_id=sale_item_id,
        note=note,
        lines=lines,
        allowed_move_types=SUPPORTED_MOVE_TYPES,
    )


def _record_move(
    conn: Any,
    *,
    move_id: str,
    sim_run_id: str,
    lot_id: str,
    move_type: str,
    quantity_kg: Decimal,
    moved_at: date,
    reason_code: str,
    sale_item_id: str | None,
    note: str | None,
    lines: Sequence[MoveLine],
    allowed_move_types: frozenset[str],
) -> LedgerResult:
    """원장 쓰기의 **공통 경로**. 두 공개 진입점이 이 하나를 나눠 쓴다.

    ★ **어떤 Move Type 을 받을지는 부르는 쪽이 정한다** (`allowed_move_types`).
      그래서 `DISPOSE` 가 열려도 `record_inventory_move` 는 여전히 IN·OUT 만 받는다 —
      폐기가 아무 데서나 새어 들어오지 않는 자리가 여기다.
    """
    quantity, line_quantities = validated_move(
        move_id=move_id,
        move_type=move_type,
        quantity_kg=quantity_kg,
        lines=lines,
        allowed_move_types=allowed_move_types,
    )

    # ★ **아무것도 읽거나 쓰기 전에 원장 잠금을 잡는다.** 기다리는 쪽이 아직 아무
    #   자원도 안 쥐고 있어야 순환이 생길 자리가 없다 (`lock_ledger_writes` 참조).
    lock_ledger_writes(conn)

    requested = {
        "sim_run_id": sim_run_id,
        "lot_id": lot_id,
        "sale_item_id": sale_item_id,
        "move_type": move_type,
        "quantity_kg": quantity,
        "moved_at": moved_at,
        "reason_code": reason_code,
        "note": note,
    }
    existing = select_existing_move(conn, move_id=move_id)
    if existing is not None:
        # 🔴 **Lot 을 보기 전에 사실부터 대조한다.** Lot 을 먼저 읽으면 *"같은 move_id
        #    인데 없는 Lot 을 가리킨다"* 가 `LotNotFound` 로 나간다 — 그것은 부재가
        #    아니라 **멱등 키 충돌**이고, 부르는 쪽이 둘을 가려야 한다.
        assert_same_move_facts(move_id=move_id, existing=existing, requested=requested)
        # 🔴 **Header 만 보면 안 된다.** 같은 20kg 이어도 어느 Pallet 에서 어느 자리로
        #    나갔는지가 다르면 다른 사실이다 — Header 만 대조하면 그 차이가 에러 없이
        #    삼켜지고, 두 번째 요청의 Line 은 장부 어디에도 안 남는다.
        assert_same_move_lines(
            move_id=move_id,
            existing=select_existing_move_lines(conn, move_id=move_id),
            requested=[
                (line.pallet_id, line.location_id, line_quantity, line.note)
                for line, line_quantity in zip(lines, line_quantities, strict=True)
            ],
        )
        # ★ 같은 건이다. 잔량을 **다시 바꾸지 않는다.** 실패가 아니라 이미 반영됨이다.
        #   ⚠️ 여기서는 Lot 을 **읽기만** 한다 — 바꿀 것이 없는데 행 잠금을 잡으면
        #      교착 면적만 넓어진다.
        return LedgerResult(
            move_id=move_id,
            applied=False,
            remaining_qty_kg=select_current_remaining(
                conn, lot_id=lot_id, sim_run_id=sim_run_id
            ),
            line_count=0,
        )

    current_remaining, original = lock_lot_row(
        conn, lot_id=lot_id, sim_run_id=sim_run_id
    )
    next_remaining = next_remaining_qty(
        move_type=move_type,
        quantity=quantity,
        current_remaining=current_remaining,
        original=original,
        lot_id=lot_id,
        move_id=move_id,
    )

    insert_move(conn, move_id=move_id, values=requested)
    for line, line_quantity in zip(lines, line_quantities, strict=True):
        insert_move_line(
            conn, move_id=move_id, lot_id=lot_id, line=line, quantity=line_quantity
        )
    update_lot_remaining(
        conn,
        lot_id=lot_id,
        sim_run_id=sim_run_id,
        next_remaining=next_remaining,
        move_id=move_id,
    )

    return LedgerResult(
        move_id=move_id,
        applied=True,
        remaining_qty_kg=next_remaining,
        line_count=len(line_quantities),
    )
