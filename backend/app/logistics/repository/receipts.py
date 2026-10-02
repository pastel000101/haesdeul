"""도착 Receipt SQL — `(sim_run_id, inbound_id)` 로 읽기 · `ARRIVED` INSERT.

받은 연결로 실행만 한다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from psycopg import sql

from app.logistics.repository.rows import cell, get_db_schema
from app.logistics.schemas.purchase_detail import PurchaseDetail
from app.logistics.schemas.receipts import RECEIPT_FACT_SOURCE, STATUS_ON_ARRIVAL

#: 한 번에 둘까지만 읽는다. 셋을 가르는 데 그 이상이 필요 없다.
#:
#: 정확한 개수를 세도 결정이 달라지지 않는다 — 2건이든 5건이든 우리는 어느
#: 하나도 고르지 않고 멈춘다. 그래서 개수 대신 부딪힌 두 `receipt_id` 를
#: 남긴다. 조사에 쓸모 있는 쪽은 그쪽이다.
_CORRUPTION_PROBE_LIMIT = 2


def select_receipt_rows(
    conn: Any, *, sim_run_id: str, inbound_id: str
) -> list[tuple[Any, Any]]:
    """`(sim_run_id, inbound_id)` 의 Receipt 행들 — `(receipt_id, receipt_status)` 로.

    최대 `_CORRUPTION_PROBE_LIMIT` 줄까지만 읽는다 — 둘 이상인지만 알면 된다.
    """
    schema = sql.Identifier(get_db_schema())
    # `ORDER BY receipt_id` 는 깨진 경우의 메시지를 결정적으로 만든다.
    # 같은 손상 상태를 두 번 조회하면 같은 두 id 가 같은 순서로 나온다.
    query = sql.SQL(
        """
        SELECT receipt_id, receipt_status
        FROM {}.inbound_receipts
        WHERE sim_run_id = %s
          AND inbound_id = %s
        ORDER BY receipt_id
        LIMIT {}
        """
    ).format(schema, sql.Literal(_CORRUPTION_PROBE_LIMIT))

    with conn.cursor() as cursor:
        cursor.execute(query, (sim_run_id, inbound_id))
        # `fetchone()` 을 쓰지 않는다. 그것은 2건 이상을 조용히 첫 행으로 돌려준다 —
        # 무결성 위반이 정상 응답으로 나가는 자리가 정확히 거기다.
        rows = cursor.fetchall()
    return [
        (cell(row, 0, "receipt_id"), cell(row, 1, "receipt_status")) for row in rows
    ]


def insert_arrived_receipt(
    conn: Any,
    *,
    receipt_id: str,
    sim_run_id: str,
    inbound_id: str,
    expected_arrival_date: date,
    purchase_detail: PurchaseDetail,
) -> None:
    """도착 Receipt 한 줄 INSERT (`ARRIVED` · `SCENARIO_SIMULATED`). 잠금·재조회는 부르는 쪽이다."""
    schema = sql.Identifier(get_db_schema())
    # nullable 칸을 아예 안 적는다. 값을 지어내는 대신 DB 기본값(NULL)에 맡긴다.
    # accepted/hold/rejected 수량 · receiving_location_id · 팔레트 수 · received_by
    # 는 검수·적치 단계의 사실이고, `created_at`/`updated_at` 은 DB DEFAULT 다.
    insert_query = sql.SQL(
        """
        INSERT INTO {}.inbound_receipts (
            receipt_id, sim_run_id, inbound_id, purchase_item_id, item_id,
            arrived_at, ordered_qty_kg, receipt_status, fact_source
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
    ).format(schema)

    with conn.cursor() as cursor:
        cursor.execute(
            insert_query,
            (
                receipt_id,
                sim_run_id,
                inbound_id,
                purchase_detail.purchase_item_id,
                purchase_detail.item_id,
                # 예정일 그대로다 — 연체분도 옮기지 않는다.
                expected_arrival_date,
                # 권위 있는 매입 사실이다. 일정 수량으로 덮어쓰지 않는다.
                purchase_detail.quantity_kg,
                STATUS_ON_ARRIVAL,
                RECEIPT_FACT_SOURCE,
            ),
        )
