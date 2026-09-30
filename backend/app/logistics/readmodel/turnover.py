"""Lot 회전 읽기 — 받은 연결로 회전 재료 행을 읽어 `LotTurnover` 로 편다.

★ 2026-09-30 재구성 BL-015: `logistics/turnover.py` 의 `load_lot_turnover` 를 옮겼다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.logistics.domain.turnover import lot_turnover_from_row
from app.logistics.repository.turnover import select_lot_turnover_rows
from app.logistics.schemas.turnover import LotTurnover


def load_lot_turnover(
    conn: Any, *, sim_run_id: str, as_of: date, lot_id: str | None = None
) -> tuple[LotTurnover, ...]:
    """창고에 남아 있는 Lot 들의 회전·신선도 파생값. **읽기만 한다.**

    🔴 **`item_turnover_policies` 를 `LEFT JOIN` 한다.** 그 표는 실측 3품목뿐이라
       `INNER JOIN` 하면 계약 밖 품목의 재고가 **조회에서 통째로 사라진다**
       (DDL 주석이 같은 경고를 적어 두었다).

    ★ **`repository` 의 Lot 조회와 같은 눈으로 고른다** — `remaining_qty_kg > 0` ·
      `received_at <= as_of`. 상태로 거르지 않는다: 검수·격리 재고도 공간을
      점유하고 회전 시계도 돈다.

    ⚠️ **아무것도 바꾸지 않는다.** 가용재고 판정도 여기서 하지 않는다 — 그것은
       `tools.build_inventory_by_item` 몫이고, 이 함수는 **파생 사실만** 낸다.

    :param lot_id: 주면 그 Lot 하나만 읽는다 (폐기 확정이 쓴다).
    """
    rows = select_lot_turnover_rows(conn, sim_run_id=sim_run_id, as_of=as_of, lot_id=lot_id)
    return tuple(lot_turnover_from_row(row, as_of=as_of) for row in rows)
