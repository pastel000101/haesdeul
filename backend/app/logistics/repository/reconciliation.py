"""고착 입고 일정 정리의 계보 읽기 — 그 일정에 Receipt · Lot · 원장 IN 이 붙었나.

★ 2026-09-30 재구성 BL-015: `logistics/inbound_reconciliation.py` 에서 옮겼다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from psycopg import sql

from app.logistics.repository.rows import get_db_schema
from app.logistics.schemas.reconciliation import (
    InboundLineageAmbiguous,
    InboundReconciliationError,
    MaterializedInbound,
)

#: 🔴 원장 어휘 그대로다 (`inventory_moves.move_type` CHECK). 새 낱말을 만들지 않는다.
_IN_MOVE_TYPE = "IN"

#: 계보 조회에서 **읽기만 하는** 칸들. 이 함수는 이 표들에 한 줄도 안 쓴다.
_LINEAGE_COLUMNS = ("receipt_id", "receipt_status", "lot_id", "move_id")

#: 🔴 **둘까지만 읽는다.** 0 · 1 · 2+ 를 가르는 데 그 이상이 필요 없다
#: (`inbound_stock._AMBIGUITY_PROBE_LIMIT` 과 같은 태도).
_AMBIGUITY_PROBE_LIMIT = 2


def select_materialized_lineage(
    conn: Any, *, sim_run_id: str, inbound_id: str
) -> list[MaterializedInbound]:
    """이 `inbound_id` 에 붙은 **입고 계보**를 읽는다. 쓰기가 없다.

    ```text
    inbound_receipts.inbound_id      ← 이 축으로 찾는다 (uq: sim_run_id + inbound_id)
    inventory_lots.inbound_receipt_id  → Receipt 에서 나온 Lot
    inventory_moves.lot_id (IN)        → 그 Lot 의 원장 입고
    ```

    🔴 **수량·날짜·품목으로 찾지 않는다.** 실데이터에 같은 규모의 Receipt 가 여럿
       있고 `inbound_id` 는 서로 다르다 — 닮았다는 이유로 같은 입고라고 하면 **남의
       입고를 지운다.** 계보는 `inbound_id` 한 축으로만 따라간다.

    ★ **`LEFT JOIN` 이다.** Receipt 만 있고 Lot 이 없는 상태(도착·검수 중)도 계보가
      **있는** 것이다 — 그 건도 일정만 걷어서 될 일이 아니다.

    ```text
    Receipt 0건    계보 없음      → 걷기 후보
    Receipt 1건    도착 처리 시작  → ScheduleAlreadyMaterialized (Lot·IN 여부는 사유에)
    Receipt 2건+   모호           → InboundLineageAmbiguous  🔴 첫 행을 안 고른다
    ```

       ★ Lot 도 같은 태도다. 한 Receipt 에 Lot 이 여럿이면 그 행들이 **전부** 사유에
         실린다 — 하나만 보여 주고 나머지를 감추지 않는다.

    🔴 **Receipt 가 0건이면 Lot 도 0건이다.** `inventory_lots.inbound_receipt_id` 가
       Receipt 를 FK 로 가리키므로, 붙을 Receipt 가 없으면 붙은 Lot 도 없다 — 그래서
       Receipt 축 하나로 세는 것으로 충분하다.
    """
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT r.receipt_id, r.receipt_status, l.lot_id, m.move_id
                FROM {}.inbound_receipts AS r
                LEFT JOIN {}.inventory_lots AS l
                       ON l.inbound_receipt_id = r.receipt_id
                LEFT JOIN {}.inventory_moves AS m
                       ON m.lot_id = l.lot_id AND m.move_type = %s
                WHERE r.sim_run_id = %s AND r.inbound_id = %s
                ORDER BY r.receipt_id, l.lot_id, m.move_id
                """
            ).format(schema, schema, schema),
            (_IN_MOVE_TYPE, sim_run_id, inbound_id),
        )
        rows = cursor.fetchall()
    계보 = [MaterializedInbound(*_row_values(row)) for row in rows]
    # ★ 행 수가 아니라 **Receipt 수**로 센다 — Lot 이 여럿이면 한 Receipt 도 여러 행이다.
    receipt_ids = {한줄.receipt_id for 한줄 in 계보}
    if len(receipt_ids) >= _AMBIGUITY_PROBE_LIMIT:
        raise InboundLineageAmbiguous(
            f"같은 inbound_id 에 Receipt 가 둘 이상이다 (inbound_id={inbound_id!r},"
            f" sim_run_id={sim_run_id!r}): {sorted(receipt_ids)!r}."
            " 어느 것도 고르지 않는다 — 골라 버리면 나머지가 조용히 없는 것이 된다."
        )
    return 계보


def _row_values(row: Any) -> tuple[Any, ...]:
    """`dict_row` 든 tuple 이든 같은 순서로 읽는다.

    ★ 커넥션의 `row_factory` 가 호출자마다 다르다 — 공통 풀 연결(`app.core.db`)은
      `dict_row` 를 쓰고, 남의 트랜잭션을 물려받으면 기본 tuple 일 수 있다.
    """
    if isinstance(row, dict):
        return tuple(row[이름] for 이름 in _LINEAGE_COLUMNS)
    if isinstance(row, Sequence):
        return tuple(row[i] for i in range(len(_LINEAGE_COLUMNS)))
    raise InboundReconciliationError(f"입고 계보 행을 못 읽는다: {row!r}")
