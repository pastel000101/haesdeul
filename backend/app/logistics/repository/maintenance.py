"""자동 유지보수 SQL — 잔량 0 인데 Pallet 이 남은 Lot.

★ 2026-09-30 재구성 BL-015: `logistics/auto_maintenance.py` 에서 옮겼다.
"""

from __future__ import annotations

from typing import Any

from psycopg import sql

from app.logistics.repository.rows import get_db_schema
from app.logistics.schemas.vocabulary import OCCUPYING_PALLET


def lots_needing_pallet_cleanup(
    conn: Any, *, sim_run_id: str
) -> list[str]:
    """잔량이 **이미 0** 인데 자리를 아직 잡고 있는 Lot 들.

    ★ `load_lot_turnover` 는 `remaining_qty_kg > 0` 만 돌려주므로 이 축이 그 조회에
      안 잡힌다 — 과거에 출고·폐기로 0 이 됐는데 Pallet 만 남은 경우다
      (수량 정리와 물리 자리는 원래 다른 사실이라 갈릴 수 있다).

    🔴 **여기서 *비워도 되나* 를 판정하지 않는다.** 이 조회는 *"어느 Lot 을 물어볼까"*
       까지만 고른다 — 실제 판정(`remaining == 0` · 상태 · 자리)은 `empty_pallet` 이
       잠금 안에서 다시 한다.
    """
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT DISTINCT l.lot_id
                FROM {}.inventory_lots AS l
                JOIN {}.pallets AS p ON p.lot_id = l.lot_id
                WHERE l.sim_run_id = %s
                  AND l.remaining_qty_kg = 0
                  AND p.status = ANY(%s)
                ORDER BY l.lot_id
                """
            ).format(schema, schema),
            (sim_run_id, sorted(OCCUPYING_PALLET)),
        )
        행들 = cursor.fetchall()
    # ★ 커넥션의 `row_factory` 가 호출자마다 다르다 — dict 든 tuple 이든 같게 읽는다.
    return [str(행["lot_id"] if isinstance(행, dict) else 행[0]) for 행 in 행들]
