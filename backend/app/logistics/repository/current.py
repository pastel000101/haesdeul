"""현재 시점 SQL — 활성 정책 · 그날 fixture · 창고에 실재하는 Lot · 품목 보관 정책 · 출고가 잡은 몫.

★ 2026-09-30 재구성 BL-015: `logistics/repository.py` 에서 옮겼다. **받은 연결로만 읽는다.** 종전의
  «연결이 없으면 SQL 마다 조회 연결을 빌리는» 경로(`_fetch` → `logistics/db.fetch_all`)는 그 경계
  그대로 `readmodel/current` 로 옮겼다 — 연결 없이 부르는 쪽에는 그 모듈이 SELECT 마다 조회 연결을
  따로 빌려 여기 함수에 넘긴다. 그래서 여기 함수 하나는 SELECT 하나다.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from psycopg import sql

from app.logistics.repository.rows import dict_rows, get_db_schema
from app.logistics.schemas.snapshot import (
    LOGISTICS_POLICY_VERSION,
    ItemStoragePolicyFact,
    OutboundCommitment,
)
from app.logistics.schemas.vocabulary import (
    ASSIGNED_ALLOCATION,
    HOLDING_ALLOCATION,
    HOLDING_RESERVATION,
    USAGE_SCOPE,
)


def _fetch(query: Any, params: Any, conn: Any) -> list[dict[str, Any]]:
    """받은 연결로 읽는다. 연결을 빌리는 곳은 이 모듈이 아니다(`readmodel/current._read_on`)."""
    return dict_rows(conn, query, params)


def get_item_storage_policies(conn: Any) -> list[ItemStoragePolicyFact]:
    """품목 단위 보관 정책을 조회한다.

    Lot 목록에서 역산하지 않는다 — 새로 매입하려는 품목은 현재 재고가 0kg일 수 있고
    그때도 보관한계는 알아야 한다. 정책 테이블 자체를 기준으로 읽는다.
    """
    schema = sql.Identifier(get_db_schema())
    rows = _fetch(
        sql.SQL(
            """
            SELECT
                i.item_name,
                p.operational_limit_days,
                p.medium_grade_factor
            FROM {}.item_storage_policies p
            JOIN {}.items i ON i.item_id = p.item_id
            ORDER BY i.item_name
            """
        ).format(schema, schema),
        [],
        conn,
    )
    return [_item_storage_policy_from_row(row) for row in rows]


def _item_storage_policy_from_row(row: dict[str, object]) -> ItemStoragePolicyFact:
    item = row.get("item_name")
    limit_days = row.get("operational_limit_days")
    medium_factor = row.get("medium_grade_factor")
    if not isinstance(item, str) or not item:
        raise TypeError("Item storage policy item_name must be a non-empty string")
    # 값이 없으면 없는 대로 둔다 — 0이나 0.6 같은 기본값을 코드에서 지어내지 않는다.
    if limit_days is not None and (isinstance(limit_days, bool) or not isinstance(limit_days, int)):
        raise TypeError(f"Item storage policy operational_limit_days must be an int: {item}")
    if medium_factor is not None and (
        isinstance(medium_factor, bool) or not isinstance(medium_factor, Decimal)
    ):
        raise TypeError(f"Item storage policy medium_grade_factor must be a Decimal: {item}")
    return ItemStoragePolicyFact(
        item=item,
        operational_limit_days=limit_days,
        medium_grade_factor=medium_factor,
    )


def select_holding_allocation_rows(conn: Any, *, sim_run_id: str) -> list[dict[str, Any]]:
    """출고가 잡아 둔 몫 ① 살아있는 할당 — Lot 축. 읽는 규율은 `outbound_commitments_from`."""
    schema = sql.Identifier(get_db_schema())
    return _fetch(
        sql.SQL(
            """
            SELECT a.lot_id, i.item_name, SUM(a.allocated_qty_kg) AS quantity_kg
            FROM {}.inventory_allocations a
            JOIN {}.inventory_reservations r ON r.reservation_id = a.reservation_id
            JOIN {}.inventory_lots l ON l.lot_id = a.lot_id
            JOIN {}.items i ON i.item_id = l.item_id
            WHERE r.sim_run_id = %s AND a.status = ANY(%s)
            GROUP BY a.lot_id, i.item_name
            ORDER BY a.lot_id
            """
        ).format(schema, schema, schema, schema),
        [sim_run_id, sorted(HOLDING_ALLOCATION)],
        conn,
    )


def select_unallocated_reservation_rows(conn: Any, *, sim_run_id: str) -> list[dict[str, Any]]:
    """출고가 잡아 둔 몫 ② 미할당 예약 잔여 — 품목 축. 읽는 규율은 `outbound_commitments_from`."""
    schema = sql.Identifier(get_db_schema())
    return _fetch(
        sql.SQL(
            """
            SELECT i.item_name,
                   SUM(GREATEST(r.reserved_qty_kg - COALESCE(a.assigned_qty_kg, 0), 0))
                       AS quantity_kg
            FROM {}.inventory_reservations r
            JOIN {}.items i ON i.item_id = r.item_id
            LEFT JOIN (
                SELECT reservation_id, SUM(allocated_qty_kg) AS assigned_qty_kg
                FROM {}.inventory_allocations
                WHERE status = ANY(%s)
                GROUP BY reservation_id
            ) a ON a.reservation_id = r.reservation_id
            WHERE r.sim_run_id = %s AND r.status = ANY(%s)
            GROUP BY i.item_name
            ORDER BY i.item_name
            """
        ).format(schema, schema, schema),
        [
            sorted(ASSIGNED_ALLOCATION),
            sim_run_id,
            sorted(HOLDING_RESERVATION),
        ],
        conn,
    )


def get_outbound_commitments(conn: Any, *, sim_run_id: str) -> list[OutboundCommitment]:
    """출고가 **이미 잡아 둔 몫** — 받은 연결 하나로 할당 · 예약 SELECT 를 차례로 읽는다.

    규율은 `outbound_commitments_from`. 연결 없이 읽는 쪽(`readmodel/current`)은 두 SELECT 를
    따로 빌린 조회 연결로 읽어 같은 행 읽기에 넘긴다.
    """
    return outbound_commitments_from(
        select_holding_allocation_rows(conn, sim_run_id=sim_run_id),
        select_unallocated_reservation_rows(conn, sim_run_id=sim_run_id),
    )


def outbound_commitments_from(
    allocation_rows: list[dict[str, Any]], reservation_rows: list[dict[str, Any]]
) -> list[OutboundCommitment]:
    """출고가 **이미 잡아 둔 몫** — 위 두 SELECT 의 행을 읽는다. `outbound.py` 와 같은 규율로 센다.

    ```text
    lot_id 있음   살아있는 할당      ALLOCATED · PICKED
    lot_id 없음   미할당 예약 잔여   reserved − (ALLOCATED·PICKED·SHIPPED)  · 음수는 0
    ```

    🔴 **`SHIPPED` 를 할당 쪽에서는 빼고 예약 쪽에서는 뺀다.** 헷갈리는 자리라 이유를
       적는다.

    ```text
    할당 축   SHIPPED 는 제외   원장 OUT 이 remaining_qty_kg 에서 이미 덜어냈다
    예약 축   SHIPPED 도 포함   그 예약이 더 이상 새로 잡아 둘 필요가 없는 몫이다
    ```

       ⚠️ 이 두 줄이 `outbound._HOLDING_ALLOCATION` · `ASSIGNED_ALLOCATION` 과 **글자
          그대로 같아야 한다.** 다르면 같은 재고를 두 곳이 다르게 세고, 매입에 나가는
          `inventory_by_item` 과 예약이 실제로 잡을 수 있는 양이 어긋난다.

    ⚠️ **놓아준 예약(`RELEASED`·`CANCELLED`)은 세지 않는다** — 돌려준 몫이다.

    ★ 빈 목록은 *"0건 확인"* 이다. 못 읽은 것과 구분하려고 예외를 삼키지 않는다.

    ★ 2026-09-30 재구성 BL-015 보완: `get_outbound_commitments` 의 몸통을 SELECT 둘과 이 행 읽기로
      나눴다(SQL 문면 · 순서 그대로). 연결 없이 부르는 쪽이 종전처럼 SELECT 마다 조회 연결을
      따로 빌리게 하려는 것이다 — 빌리는 곳은 `readmodel/current` 다.
    """
    commitments = [
        OutboundCommitment(
            item=_text(row.get("item_name"), 칸="item_name"),
            lot_id=_text(row.get("lot_id"), 칸="lot_id"),
            quantity_kg=_decimal(row.get("quantity_kg"), 칸="allocated_qty_kg"),
        )
        for row in allocation_rows
        if _decimal(row.get("quantity_kg"), 칸="allocated_qty_kg") > 0
    ]
    commitments += [
        OutboundCommitment(
            item=_text(row.get("item_name"), 칸="item_name"),
            lot_id=None,
            quantity_kg=_decimal(row.get("quantity_kg"), 칸="unallocated_qty_kg"),
        )
        for row in reservation_rows
        if _decimal(row.get("quantity_kg"), 칸="unallocated_qty_kg") > 0
    ]
    return commitments


def _text(value: object, *, 칸: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"Outbound commitment {칸} must be a non-empty str")
    return value


def _decimal(value: object, *, 칸: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise TypeError(f"Outbound commitment {칸} must be a Decimal")
    return value


def select_policy_rows(conn: Any) -> list[dict[str, Any]]:
    """물류 MVP 범위의 활성 정책 행 (`agent_policy_config` domain=logistics)."""
    query = sql.SQL(
        """
        SELECT
            policy_key,
            value_kind,
            value_numeric,
            value_text,
            value_json,
            source_ref,
            policy_version,
            usage_scope
        FROM {}.agent_policy_config
        WHERE domain = %s
          AND policy_version = %s
          AND usage_scope = %s
          AND is_active = TRUE
        """
    ).format(sql.Identifier(get_db_schema()))
    rows = _fetch(
        query,
        ["logistics", LOGISTICS_POLICY_VERSION, USAGE_SCOPE],
        conn,
    )
    return rows


def select_runtime_fixture_rows(
    conn: Any, *, as_of: date, sim_run_id: str | None
) -> list[dict[str, Any]]:
    """그날 활성 MVP runtime fixture 행들 — 축 `(sim_run_id, as_of, usage_scope)`."""
    schema = sql.Identifier(get_db_schema())
    # ★ 파라미터 순서를 안 바꾼다 — 실행 조건은 **뒤에** 붙인다. 앞을 흔들면 이
    #   질의를 파라미터로 재는 검사들이 축과 무관하게 깨진다.
    params: list[object] = [USAGE_SCOPE, as_of]
    실행조건 = sql.SQL("")
    if sim_run_id is not None:
        실행조건 = sql.SQL("AND sim_run_id = %s")
        params.append(sim_run_id)
    rows = _fetch(
        sql.SQL(
            """
            SELECT
                fixture_id,
                sim_run_id,
                as_of,
                in_transit_status,
                confirmed_inbound_status,
                confirmed_outbound_status,
                usage_scope,
                evidence_grade,
                source_ref,
                approved_by
            FROM {}.logistics_runtime_fixture
            WHERE usage_scope = %s
              AND as_of = %s
              AND is_active = TRUE
              {}
            ORDER BY fixture_id
            """
        ).format(schema, 실행조건),
        params,
        conn,
    )
    return rows


def select_current_lot_rows(conn: Any, *, sim_run_id: str, as_of: date) -> list[dict[str, Any]]:
    """잔량이 남아 창고에 실재하는 Lot 행 + 보관 정책 (현재값 · `remaining_qty_kg > 0`)."""
    schema = sql.Identifier(get_db_schema())
    inventory_rows = _fetch(
        sql.SQL(
            """
            SELECT
                l.lot_id,
                i.item_name,
                l.grade,
                l.received_at,
                l.unit_cost_krw_per_kg,
                l.remaining_qty_kg,
                l.status,
                l.storage_zone,
                p.operational_limit_days,
                p.medium_grade_factor
            FROM {}.inventory_lots l
            JOIN {}.items i ON i.item_id = l.item_id
            JOIN {}.item_storage_policies p ON p.item_id = l.item_id
            WHERE l.sim_run_id = %s
              AND l.received_at <= %s
              AND l.remaining_qty_kg > 0
            ORDER BY l.lot_id
            """
        ).format(schema, schema, schema),
        [sim_run_id, as_of],
        conn,
    )
    return inventory_rows
