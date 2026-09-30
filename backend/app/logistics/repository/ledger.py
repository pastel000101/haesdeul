"""재고 원장 SQL — Lot 잔량 잠금 · 읽기 · 기존 Move/Line · INSERT · 잔량 UPDATE.

★ 2026-09-30 재구성 BL-015: `logistics/ledger.py` 에서 옮겼다. 받은 연결로 실행만 하고 commit 하지
  않는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from psycopg import sql

from app.logistics.repository.rows import cell, get_db_schema
from app.logistics.schemas.ledger import (
    IDENTITY_COLUMNS,
    LINE_IDENTITY_COLUMNS,
    LotNotFound,
    MoveLine,
)


def lock_lot_row(
    conn: Any, *, lot_id: str, sim_run_id: str
) -> tuple[Decimal, Decimal]:
    """대상 Lot 을 잠그고 (현재 잔량, 최초 수량)을 읽는다.

    ★ **이 모듈의 동시성 방어는 잠금 둘이 나눠 맡는다.**

    ```text
    lock_ledger_writes()   원장 쓰기 전체를 전역으로 직렬화한다 (advisory xact lock)
    여기의 FOR UPDATE       수량을 바꾸는 그 Lot 행을 잡아 둔다
    ```

    🔴 **둘 다 바깥 트랜잭션이 끝날 때까지 풀리지 않는다.** 그래서 잠금 순서가
       **원장 잠금 → Lot 행** 한 방향으로 고정되어야 하고, 이 함수는 원장 잠금을 이미
       쥔 뒤에만 불린다 (`record_inventory_move` 의 ② → ④).

    ⚠️ **전역 직렬화가 있으니 이 행 잠금이 남아도는 것은 아니다.** 원장 잠금은 이 모듈을
       지나는 쓰기만 세우고, `inventory_lots` 는 원장 밖에서도 갱신될 수 있는 표다
       (`database/seed/demo/mvp_demo_remove_pimanul.sql` 같은 직접 UPDATE 가 실제로 있었다).
       행 잠금이 없으면 그런 경로와 겹칠 때 두 쪽이 같은 잔량을 읽고 각자 빼서, 각각은
       검사를 통과하는데 합쳐 놓으면 음수가 된다 — DB CHECK 이 마지막에 잡더라도 그때는
       어느 쪽이 틀렸는지 알 수 없다.

    ★ `sim_run_id` 를 조건에 넣는다 — 잔량을 바꾸는 쪽은 *"어느 실행의 장부인가"* 를
      알고 부른다 (`transition.persist_inventory` 의 WHERE 와 같은 규율).
    """
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT remaining_qty_kg, original_qty_kg
                FROM {}.inventory_lots
                WHERE lot_id = %s AND sim_run_id = %s
                FOR UPDATE
                """
            ).format(schema),
            (lot_id, sim_run_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise LotNotFound(
                f"재고 Lot 이 없다 (lot_id={lot_id}, sim_run_id={sim_run_id})."
                " 새로 만들지 않는다 — Lot 생성은 입고 단계 소유다."
            )
        return cell(row, 0, "remaining_qty_kg"), cell(row, 1, "original_qty_kg")


def select_current_remaining(
    conn: Any, *, lot_id: str, sim_run_id: str
) -> Decimal:
    """Lot 잔량을 **읽기만** 한다. 멱등 재시도가 현재값을 돌려줄 때 쓴다.

    ★ `FOR UPDATE` 를 걸지 않는다 — 이 경로는 아무것도 바꾸지 않는다. 바꾸지 않는데
      행 잠금을 잡으면 교착이 날 수 있는 면적만 넓어진다.
    """
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT remaining_qty_kg
                FROM {}.inventory_lots
                WHERE lot_id = %s AND sim_run_id = %s
                """
            ).format(schema),
            (lot_id, sim_run_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise LotNotFound(
                f"재고 Lot 이 없다 (lot_id={lot_id}, sim_run_id={sim_run_id})."
                " 새로 만들지 않는다 — Lot 생성은 입고 단계 소유다."
            )
        return cell(row, 0, "remaining_qty_kg")


def select_existing_move(conn: Any, *, move_id: str) -> dict[str, Any] | None:
    """같은 `move_id` 가 이미 있으면 그 사실들을 돌려준다.

    ★ 새 멱등 컬럼을 만들지 않았다 — `inventory_moves_pkey` 가 이미 `move_id` 라
      그 하나로 *"같은 건인가"* 를 물을 수 있다. 마이그레이션이 필요 없다.
    """
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT sim_run_id, lot_id, sale_item_id, move_type,
                       quantity_kg, moved_at, reason_code, note
                FROM {}.inventory_moves
                WHERE move_id = %s
                """
            ).format(schema),
            (move_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {name: cell(row, index, name) for index, name in enumerate(IDENTITY_COLUMNS)}


def select_existing_move_lines(
    conn: Any, *, move_id: str
) -> list[tuple[Any, ...]]:
    """이미 있는 Move 의 Line 사실들.

    ★ **중복 판정에 걸렸을 때만 읽는다.** 정상 경로(새 Move)는 이 조회를 지나지 않는다.
    ★ `ORDER BY` 를 걸지 않는다 — 순서는 업무 사실이 아니라서 multiset 으로 대조한다
      (`assert_same_move_lines`). 정렬해도 결과가 같으니 DB 에 일을 시키지 않는다.
    """
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT pallet_id, location_id, quantity_kg, note
                FROM {}.inventory_move_lines
                WHERE move_id = %s
                """
            ).format(schema),
            (move_id,),
        )
        return [
            tuple(cell(row, index, name) for index, name in enumerate(LINE_IDENTITY_COLUMNS))
            for row in cursor.fetchall()
        ]


def insert_move(
    conn: Any, *, move_id: str, values: Mapping[str, Any]
) -> None:
    """원장 Header 한 줄.

    ★ `ON CONFLICT` 를 쓰지 않는다 — 중복은 위에서 이미 사실 대조로 가렸다.
      여기서 조용히 넘기면 *"같은 id 인데 다른 사실"* 이 통과한다.
    """
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                INSERT INTO {}.inventory_moves (
                    move_id, sim_run_id, lot_id, sale_item_id,
                    move_type, quantity_kg, moved_at, reason_code, note
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
            ).format(schema),
            (
                move_id,
                values["sim_run_id"],
                values["lot_id"],
                values["sale_item_id"],
                values["move_type"],
                values["quantity_kg"],
                values["moved_at"],
                values["reason_code"],
                values["note"],
            ),
        )


def insert_move_line(
    conn: Any,
    *,
    move_id: str,
    lot_id: str,
    line: MoveLine,
    quantity: Decimal,
) -> None:
    """Pallet 단위 내역 한 줄.

    ★ `lot_id` 는 Header 의 것을 그대로 쓴다 — 호출자가 따로 주지 않는다.
      복합 FK 둘(`fk_move_lines_move_lot` · `fk_move_lines_pallet_lot`)이 Line 의 Lot 을
      Header·Pallet 과 일치시키는데, 호출자가 다른 Lot 을 넣을 수 있게 두면
      그 제약에 걸리는 것이 **업무 실수가 아니라 API 실수**가 된다.
    """
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                INSERT INTO {}.inventory_move_lines (
                    move_id, lot_id, pallet_id, location_id, quantity_kg, note
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """
            ).format(schema),
            (move_id, lot_id, line.pallet_id, line.location_id, quantity, line.note),
        )


def update_lot_remaining(
    conn: Any,
    *,
    lot_id: str,
    sim_run_id: str,
    next_remaining: Decimal,
    move_id: str,
) -> None:
    """Lot 잔량을 반영값으로 놓는다.

    ★ `status` 를 건드리지 않는다 — 잔량이 0 이 돼도 `DEPLETED` 로 바꾸지 않고,
      IN 이 들어와도 `ACTIVE` 로 되돌리지 않는다. 상태 결정은 입고·출고 단계 소유다
      (미결 사항으로 보고했다).

      ⚠️ 그래도 기존 Agent 계약은 깨지지 않는다 —
      `readmodel/current.get_current_logistics_read()` 가 `remaining_qty_kg > 0` 으로 거르므로
      잔량 0 인 Lot 은 status 와 무관하게 Snapshot 에서 빠진다.

    ★ `rowcount` 를 본다. 잠글 때 있던 행이라 0 이 나올 수 없지만, 나오면 잠금 조건과
      쓰기 조건이 갈렸다는 뜻이라 조용히 지나가면 안 된다.
    """
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                UPDATE {}.inventory_lots
                SET remaining_qty_kg = %s
                WHERE lot_id = %s AND sim_run_id = %s
                """
            ).format(schema),
            (next_remaining, lot_id, sim_run_id),
        )
        if cursor.rowcount != 1:
            raise LotNotFound(
                f"잔량을 갱신할 Lot 이 없다 (lot_id={lot_id}, sim_run_id={sim_run_id},"
                f" move_id={move_id}). 잠근 행과 쓰는 행이 갈렸다."
            )
