"""검수 SQL — 검수 읽기(한 Receipt 한 검수) · Receipt 사실 읽기 · 검수 INSERT · Receipt INSPECTED.

받은 연결로 실행만 한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg import sql

from app.logistics.repository.rows import cell, get_db_schema
from app.logistics.schemas.inspections import (
    INSPECTION_FACT_SOURCE,
    InspectionIntegrityError,
    InspectionOutcome,
    InspectionRecord,
    InvalidInspectionOutcome,
)

#: 둘까지만 읽는다. 0 · 1 · 2+ 를 가르는 데 그 이상이 필요 없다 —
#: 어차피 어느 하나도 고르지 않고 멈춘다.
_AMBIGUITY_PROBE_LIMIT = 2


_INSPECTION_COLUMNS = (
    "inspection_id",
    "verdict",
    "inspected_qty_kg",
    "accepted_qty_kg",
    "hold_qty_kg",
    "reject_qty_kg",
)


def find_inspection(conn: Any, *, receipt_id: str) -> InspectionRecord | None:
    """그 Receipt 의 검수 한 건. 읽기만 한다.

    ```text
    0행      None
    1행      InspectionRecord
    2행 이상  InspectionIntegrityError    첫 행을 고르지 않는다
    ```

    주의: DB 에 `receipt_id` UNIQUE 가 없다(실측). 그래서 여기가 유일한 방어선이다.
    """
    if not receipt_id or not receipt_id.strip():
        raise InvalidInspectionOutcome(f"검수 조회에 쓸 수 없는 receipt_id 다: {receipt_id!r}")

    schema = sql.Identifier(get_db_schema())
    query = sql.SQL(
        """
        SELECT inspection_id, verdict, inspected_qty_kg,
               accepted_qty_kg, hold_qty_kg, reject_qty_kg
        FROM {}.inbound_inspections
        WHERE receipt_id = %s
        ORDER BY inspection_id
        LIMIT {}
        """
    ).format(schema, sql.Literal(_AMBIGUITY_PROBE_LIMIT))

    with conn.cursor() as cursor:
        cursor.execute(query, (receipt_id,))
        # `fetchone()` 을 쓰지 않는다 — 2행 이상을 조용히 첫 행으로 돌려준다.
        rows = cursor.fetchall()

    if not rows:
        return None
    if len(rows) > 1:
        보인것 = [cell(row, 0, "inspection_id") for row in rows]
        raise InspectionIntegrityError(
            f"한 Receipt 에 검수가 둘 이상이다 (receipt_id={receipt_id!r}): {보인것!r} …."
            " 어느 것이 진짜인지 여기서 고르지 않는다."
        )

    값 = {name: cell(rows[0], index, name) for index, name in enumerate(_INSPECTION_COLUMNS)}
    return InspectionRecord(
        inspection_id=값["inspection_id"],
        outcome=InspectionOutcome(
            verdict=값["verdict"],
            inspected_qty_kg=값["inspected_qty_kg"],
            accepted_qty_kg=값["accepted_qty_kg"],
            hold_qty_kg=값["hold_qty_kg"],
            reject_qty_kg=값["reject_qty_kg"],
        ),
    )


def select_receipt_for_inspection(conn: Any, *, receipt_id: str) -> dict[str, Any]:
    """Receipt 의 상태와 수량. PK 로 한 행을 읽는다."""
    schema = sql.Identifier(get_db_schema())
    query = sql.SQL(
        """
        SELECT receipt_status, accepted_qty_kg, hold_qty_kg, rejected_qty_kg
        FROM {}.inbound_receipts
        WHERE receipt_id = %s
        """
    ).format(schema)
    이름 = ("receipt_status", "accepted_qty_kg", "hold_qty_kg", "rejected_qty_kg")

    with conn.cursor() as cursor:
        cursor.execute(query, (receipt_id,))
        rows = cursor.fetchall()

    if not rows:
        raise InspectionIntegrityError(
            f"검수를 적을 Receipt 가 없다: receipt_id={receipt_id!r}."
            " 도착 기록 없이 검수만 적지 않는다."
        )
    return {name: cell(rows[0], index, name) for index, name in enumerate(이름)}


def mark_receipt_inspected(
    conn: Any,
    *,
    receipt_id: str,
    outcome: InspectionOutcome,
) -> None:
    """검수 수량을 Receipt 에 옮기고 `INSPECTED` 로 넘긴다.

    주의: 칸 이름이 다르다 — 검수의 `reject_qty_kg` 가 Receipt 에서는
    `rejected_qty_kg` 다(실측).

    위치·팔레트는 건드리지 않는다. 그것들은 적치 단계의 사실이고, `PUTAWAY_DONE` 으로도
    넘기지 않는다.

    `updated_at = now()` 는 DB 의 기록 시각이다. 갱신 트리거가 없어(실측) 여기서 직접
    적는다 — 업무 시각인 `inspected_at` 과 다른 축이다.
    """
    schema = sql.Identifier(get_db_schema())
    query = sql.SQL(
        """
        UPDATE {}.inbound_receipts
        SET accepted_qty_kg = %s,
            hold_qty_kg = %s,
            rejected_qty_kg = %s,
            receipt_status = %s,
            updated_at = now()
        WHERE receipt_id = %s
        """
    ).format(schema)

    with conn.cursor() as cursor:
        cursor.execute(
            query,
            (
                outcome.accepted_qty_kg,
                outcome.hold_qty_kg,
                outcome.reject_qty_kg,
                "INSPECTED",
                receipt_id,
            ),
        )


def insert_inspection(
    conn: Any,
    *,
    inspection_id: str,
    receipt_id: str,
    inspected_at: datetime,
    inspector: str,
    outcome: InspectionOutcome,
) -> None:
    """검수 한 줄 INSERT (`SCENARIO_SIMULATED`). 잠금·기존 검수 대조는 부르는 쪽이다."""
    schema = sql.Identifier(get_db_schema())
    insert_query = sql.SQL(
        """
        INSERT INTO {}.inbound_inspections (
            inspection_id, receipt_id, inspected_at, inspector, verdict,
            inspected_qty_kg, accepted_qty_kg, hold_qty_kg, reject_qty_kg, fact_source
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
    ).format(schema)

    with conn.cursor() as cursor:
        cursor.execute(
            insert_query,
            (
                inspection_id,
                receipt_id,
                inspected_at,
                inspector,
                outcome.verdict,
                outcome.inspected_qty_kg,
                outcome.accepted_qty_kg,
                outcome.hold_qty_kg,
                outcome.reject_qty_kg,
                INSPECTION_FACT_SOURCE,
            ),
        )
