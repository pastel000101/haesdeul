"""inbound_schedules.py — 입고 예정을 **날짜에 안 묶인 업무 Entity 로** 적는다 (W3-1).

```text
승인   record_schedule   INSERT 1행                     (날짜별 복제 없음)
취소   cancel_schedule   cancelled_as_of UPDATE          (과거는 안 고친다)
조회   load_inbound_schedules  created_as_of <= as_of    (W3-2 Reader 가 재사용)
```

🔴 **입고 예정의 정본은 이 표 하나다 (W3-3 완료).**

```text
Reader   inbound_schedules                       운송 중 · 도착 처리 · Capacity
Writer   inbound_schedules                       승인 · 취소 · orphan 정리
Header   logistics_runtime_fixture.*_status      «그 축을 확인했나» 만
```

   `logistics_runtime_fixture` 의 두 JSON 칸은 더 이상 읽히지도 쓰이지도 않는다 —
   DROP 대상이다 (`database/migrations/logistics/logistics_drop_inbound_json.sql`).

🔴 **왜 표를 따로 만드는가 — 날짜별 복제가 사고를 냈다.**

   종전 입고 예정은 `in_transit_json` · `confirmed_inbound_json` 안에 **날짜마다
   복제되어** 살았고, 하루 넘김 carry-forward 가 그것을 유지했다. 그래서 미래 날짜
   행이 **먼저 열려 있으면** 그 행은 나중에 난 승인을 모른 채 굳는다.

   ```text
   2026-01-15 fixture 생성 (in_transit = [])   ← 먼저 열렸다
   2026-01-14 승인 → 01-14 행에만 기록
   2026-01-15 도착 조회 → 볼 것이 없다
   ⇒ Receipt 0 · Lot 0 · IN Move 0
   ```

   실측(2026-09-09) `INB-H1-REQ-FIRSTINB-20260113-1-1` 이 그 상태이고
   `payables … OPEN 3,066,885원` 이 그 채무를 들고 있다. 전방 전파
   (`master.day_opening_repository.opened_days_after`)가 그날을 못 본 이유는
   `master_day_openings` 에 2026-01-10 ~ 01-19 가 **한 행도 없어서**다.

   ⇒ 이 표는 **한 번 INSERT 하고 날짜로 질의한다.** 미래 날짜 행을 만들지도 고치지도
     않으므로 같은 사고가 구조적으로 재현되지 않는다.

🔴 **완료 컬럼이 없다. `status` 컬럼도 없다.**

   ```text
   완료      downstream 사실로 유도 (Receipt · Lot · IN Move)   소비자마다 다르다
   취소      cancelled_as_of 한 칸                              모순 조합이 없다
   ```

   Receipt 생성과 재고 반영 완료는 **다른 사건**이다 — `inbound_execution._receive_one`
   은 검수 사실이 없으면 `INSPECTION_FACT_UNAVAILABLE` 로 돌아서고, 그때 Receipt 만
   선 채 커밋된다. 그 상태가 며칠 이어질 수 있어 하나를 골라 `COMPLETED` 로 적으면
   나머지 소비자가 틀린다. 소비자별 종료조건은 이 파일 아래쪽 Reader 절에 있다.

🔴 **커밋도 롤백도 하지 않고 커넥션을 새로 열지 않는다.** 승인·취소가 같은 트랜잭션에서
   쓰는 다른 사실(매입 원장 · 재무 · Header status)과 **한 덩어리로 서거나 함께
   물러나야** 한다 (`transition.persist_inventory` 와 같은 규율).

★ 2026-09-30 재구성 BL-015: `logistics/inbound_schedules.py` 가
  계층별로 나뉘며 **SQL** 이 이 파일에 남았다(기존 일정 잠금 · INSERT · 취소 UPDATE · Receipt 존재 ·
  일정 보기 · 사실 날짜). 소비자별 종료조건은 `domain/inbound_schedules.py`, 요청 범위 캐시
  (`schedule_view_scope`)와 읽기 조합은 `readmodel/inbound_schedules.py`, 기록 · 취소 순서는
  `service/inbound_schedules.py`, 모델은 `schemas/inbound_schedules.py`.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from psycopg import sql

from app.logistics.repository.rows import dict_rows, schema_identifier
from app.logistics.schemas.inbound_schedules import InboundSchedule


def select_schedule(conn: Any, *, sim_run_id: str, inbound_id: str) -> dict[str, Any] | None:
    """그 일정 한 행을 **잠그고** 읽는다.

    🔴 **`FOR UPDATE` 가 이 모듈의 동시성 방어다.** 읽고-고치고-쓰는 사이에 같은
       `inbound_id` 를 겨냥한 다른 트랜잭션이 끼어들면 멱등 판정이 무너진다
       (`transition.persist_inventory` 가 fixture 행을 잠그는 것과 같은 이유).
    """
    found = dict_rows(
        conn,
        sql.SQL(
            """
            SELECT inbound_id, sim_run_id, purchase_item_id, quantity_kg,
                   expected_arrival_date, created_as_of, cancelled_as_of, source_ref, note
            FROM {}.inbound_schedules
            WHERE sim_run_id = %s AND inbound_id = %s
            FOR UPDATE
            """
        ).format(schema_identifier()),
        (sim_run_id, inbound_id),
    )
    return found[0] if found else None


def has_receipt(conn: Any, *, sim_run_id: str, inbound_id: str) -> bool:
    """그 입고의 도착 Receipt 가 있나.

    ★ `inbound_receipts` 에 `UNIQUE(sim_run_id, inbound_id)` 가 있어 0 아니면 1 이다.
    """
    found = dict_rows(
        conn,
        sql.SQL(
            "SELECT 1 AS 있음 FROM {}.inbound_receipts WHERE sim_run_id = %s AND inbound_id = %s"
        ).format(schema_identifier()),
        (sim_run_id, inbound_id),
    )
    return bool(found)


def load_inbound_schedules(
    conn: Any, *, sim_run_id: str, as_of: date
) -> tuple[InboundSchedule, ...]:
    """`as_of` 시점에 **살아 있던** 입고 예정 전부.

    ```text
    created_as_of <= as_of                              그날 이미 장부에 서 있었다
    cancelled_as_of IS NULL OR cancelled_as_of > as_of   그날 아직 취소 전이었다
    ```

    🔴 **소비자별 종료조건은 여기서 걸지 않는다 (W3-2).** Receipt · Lot · 원장 IN 을
       어디까지 봐야 하는지가 소비자마다 다르다.

    ```text
    운송 중 조회   Receipt 생성 전까지
    도착 · 용량    Lot + 원장 IN 완료 전까지
    취소 · 정리    Receipt 0건일 때만
    ```

       하나를 이 함수에 박으면 나머지가 틀린다 — 그래서 **시점 축만** 자르고,
       종료조건은 부르는 쪽이 얹는다.

    ⚠️ **W3-1 에서는 이 함수를 Runtime 이 쓰지 않는다.** Legacy JSON 과 대조하는
       자리에서만 부른다. Reader 전환은 W3-2 다.
    """
    rows = dict_rows(
        conn,
        sql.SQL(
            """
            SELECT inbound_id, sim_run_id, purchase_item_id, quantity_kg,
                   expected_arrival_date, created_as_of, cancelled_as_of, source_ref, note
            FROM {}.inbound_schedules
            WHERE sim_run_id = %(sim)s
              AND created_as_of <= %(as_of)s
              AND (cancelled_as_of IS NULL OR cancelled_as_of > %(as_of)s)
            ORDER BY expected_arrival_date, inbound_id
            """
        ).format(schema_identifier()),
        {"sim": sim_run_id, "as_of": as_of},
    )
    return tuple(
        InboundSchedule(
            inbound_id=row["inbound_id"],
            sim_run_id=row["sim_run_id"],
            purchase_item_id=row["purchase_item_id"],
            quantity_kg=row["quantity_kg"],
            expected_arrival_date=row["expected_arrival_date"],
            created_as_of=row["created_as_of"],
            cancelled_as_of=row["cancelled_as_of"],
            source_ref=row["source_ref"],
            note=row["note"],
        )
        for row in rows
    )


def schedule_fact_dates_at(
    conn: Any, *, sim_run_id: str, as_of: date, window_end: date
) -> tuple[date, ...]:
    """그날까지 **그 창의 답을 바꾼 모든 날**. 🔴 읽기만 한다.

    ```text
    목록에 들고 남    created_as_of                    장부에 선 날
                     cancelled_as_of                  내려간 날   ← 빼면 «취소가 사라진다»
    줄의 내용이 바뀜  inbound_receipts.arrived_at      has_receipt 가 참이 된 날
                     inventory_lots.received_at       stock_applied 가 ←
                     inventory_moves.moved_at (IN)    〃
    ```

    ★ **왜 다섯 축을 다 세나.** 조회의 답은 «어느 일정이 있나» + «그 일정이 어디까지
      왔나» 두 가지인데, 앞엣것만 세면 뒤엣것을 바꾼 날이 안 보인다.

    ```text
    D1  A 생성 (ETA D9)   D2  B 생성 (ETA D10)   D7  B 취소
    D8·창 D8~D11 의 답 = [A]   ← 이 답은 D7 부터 참이다. D2 라고 하면 거짓이다
    ```

    🔴 **`window_end` 가 필수인 이유 (v0.9 보정).** 창 밖 일정의 사건은 그 답을 **바꾸지
       않는다** — 세면 관측일이 근거 없이 늦어진다.

    ```text
    D1  A 생성 (ETA D9)   D7  B 생성 (ETA D100)
    D8·창 D8~D11 의 답 = [A]   ← B 는 애초에 이 답에 없다. D7 을 세면 거짓이다
    ```

    ★ **취소된 일정도 창 안이면 센다.** 지금 목록에 없어도 *"D7 에 내려가서 오늘 답이
      이렇다"* 를 만든 것이 그 취소다. 그래서 살아 있는 일정만 보지 않고 **그 창에
      속했던 일정 전체**를 본다.

    ⚠️ **창 기준은 Tool 과 글자 그대로 같아야 한다** — `expected_arrival_date <= window_end`
       하나뿐이다. `>= as_of` 같은 하한을 여기서 더하면 연체된 미도착(overdue)을 Tool 은
       세는데 Reader 는 안 세게 되어 **둘이 다른 집합을 본다.**
       (`expected_arrival_date` 는 `ScheduleConflict` 가 지켜 사실상 불변이라 창 판정에 쓸 수 있다.)

    ⚠️ **이 실행의 일정에 매달린 사건만 센다.** 실행 전체의 입고를 세면 답과 무관한
       날이 섞여 관측일이 **실제보다 늦어진다** — 늦은 쪽으로 틀리는 것도 틀린 것이다.
    """
    schema = schema_identifier()
    rows = dict_rows(
        conn,
        sql.SQL(
            """
            SELECT s.created_as_of AS changed_on
              FROM {schema}.inbound_schedules s
             WHERE s.sim_run_id = %(sim)s
               AND s.expected_arrival_date <= %(window_end)s
               AND s.created_as_of <= %(as_of)s
            UNION
            SELECT s.cancelled_as_of
              FROM {schema}.inbound_schedules s
             WHERE s.sim_run_id = %(sim)s
               AND s.expected_arrival_date <= %(window_end)s
               AND s.cancelled_as_of IS NOT NULL
               AND s.cancelled_as_of <= %(as_of)s
            UNION
            SELECT r.arrived_at
              FROM {schema}.inbound_receipts r
              JOIN {schema}.inbound_schedules s
                ON s.sim_run_id = r.sim_run_id AND s.inbound_id = r.inbound_id
             WHERE r.sim_run_id = %(sim)s
               AND s.expected_arrival_date <= %(window_end)s
               AND r.arrived_at <= %(as_of)s
            UNION
            SELECT l.received_at
              FROM {schema}.inventory_lots l
              JOIN {schema}.inbound_receipts r ON r.receipt_id = l.inbound_receipt_id
              JOIN {schema}.inbound_schedules s
                ON s.sim_run_id = r.sim_run_id AND s.inbound_id = r.inbound_id
             WHERE l.sim_run_id = %(sim)s
               AND s.expected_arrival_date <= %(window_end)s
               AND l.received_at <= %(as_of)s
            UNION
            SELECT mv.moved_at
              FROM {schema}.inventory_moves mv
              JOIN {schema}.inventory_lots l ON l.lot_id = mv.lot_id
              JOIN {schema}.inbound_receipts r ON r.receipt_id = l.inbound_receipt_id
              JOIN {schema}.inbound_schedules s
                ON s.sim_run_id = r.sim_run_id AND s.inbound_id = r.inbound_id
             WHERE mv.sim_run_id = %(sim)s
               AND s.expected_arrival_date <= %(window_end)s
               AND mv.move_type = 'IN'
               AND mv.moved_at <= %(as_of)s
             ORDER BY 1
            """
        ).format(schema=schema),
        {"sim": sim_run_id, "as_of": as_of, "window_end": window_end},
    )
    return tuple(row["changed_on"] for row in rows)


def insert_schedule(
    conn: Any,
    *,
    inbound_id: str,
    sim_run_id: str,
    purchase_item_id: str,
    quantity_kg: Decimal,
    expected_arrival_date: date,
    created_as_of: date,
    source_ref: str,
    note: str | None,
) -> None:
    """입고 일정 한 줄 INSERT. 같은 열쇠의 기존 일정 대조는 부르는 쪽이다."""
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                INSERT INTO {}.inbound_schedules (
                    inbound_id, sim_run_id, purchase_item_id, quantity_kg,
                    expected_arrival_date, created_as_of, source_ref, note
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """
            ).format(schema_identifier()),
            (
                inbound_id,
                sim_run_id,
                purchase_item_id,
                quantity_kg,
                expected_arrival_date,
                created_as_of,
                source_ref,
                note,
            ),
        )


def mark_schedule_cancelled(
    conn: Any,
    *,
    sim_run_id: str,
    inbound_id: str,
    cancelled_as_of: date,
    cancel_source_ref: str | None,
) -> None:
    """일정에 취소 날짜·근거를 적는다(아직 안 취소된 행만)."""
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                UPDATE {}.inbound_schedules
                SET cancelled_as_of = %s, cancel_source_ref = %s
                WHERE sim_run_id = %s AND inbound_id = %s AND cancelled_as_of IS NULL
                """
            ).format(schema_identifier()),
            (cancelled_as_of, cancel_source_ref, sim_run_id, inbound_id),
        )


def select_schedule_view_rows(
    conn: Any, *, sim_run_id: str, as_of: date, cutoff: datetime
) -> list[dict[str, Any]]:
    """`as_of` 에 살아 있던 일정 + 그날까지의 계보(EXISTS) 행. **한 질의다.**

    ★ `cutoff` 는 수용 0 완료를 검수 사건으로 자르는 시각이다(`timestamp_cutoff(as_of)`).
    """
    schema = schema_identifier()
    rows = dict_rows(
        conn,
        sql.SQL(
            """
            SELECT s.inbound_id, s.sim_run_id, s.purchase_item_id,
                   pi.purchase_id, pi.item_id, i.item_name,
                   s.quantity_kg, s.expected_arrival_date, s.created_as_of,
                   EXISTS (
                       SELECT 1 FROM {schema}.inbound_receipts r
                        WHERE r.sim_run_id = s.sim_run_id
                          AND r.inbound_id = s.inbound_id
                          AND r.arrived_at <= %(as_of)s
                   ) AS has_receipt,
                   EXISTS (
                       SELECT 1
                         FROM {schema}.inbound_receipts r
                         JOIN {schema}.inventory_lots l
                           ON l.inbound_receipt_id = r.receipt_id
                          AND l.sim_run_id = s.sim_run_id
                          AND l.received_at <= %(as_of)s
                         JOIN {schema}.inventory_moves mv
                           ON mv.lot_id = l.lot_id
                          AND mv.sim_run_id = s.sim_run_id
                          AND mv.move_type = 'IN'
                          AND mv.moved_at <= %(as_of)s
                        WHERE r.sim_run_id = s.sim_run_id
                          AND r.inbound_id = s.inbound_id
                          AND r.arrived_at <= %(as_of)s
                   ) AS stock_applied,
                   EXISTS (
                       SELECT 1
                         FROM {schema}.inbound_receipts r
                         JOIN {schema}.inbound_inspections ins
                           ON ins.receipt_id = r.receipt_id
                          AND ins.accepted_qty_kg = 0
                          AND ins.inspected_at < %(cutoff)s
                        WHERE r.sim_run_id = s.sim_run_id
                          AND r.inbound_id = s.inbound_id
                          AND r.arrived_at <= %(as_of)s
                          AND r.receipt_status IN ('PUTAWAY_DONE', 'CLOSED')
                   ) AS settled_without_stock
            FROM {schema}.inbound_schedules s
            LEFT JOIN {schema}.purchase_items pi
                   ON pi.purchase_item_id = s.purchase_item_id
            LEFT JOIN {schema}.items i ON i.item_id = pi.item_id
            WHERE s.sim_run_id = %(sim)s
              AND s.created_as_of <= %(as_of)s
              AND (s.cancelled_as_of IS NULL OR s.cancelled_as_of > %(as_of)s)
            ORDER BY s.expected_arrival_date, s.inbound_id
            """
        ).format(schema=schema),
        {"sim": sim_run_id, "as_of": as_of, "cutoff": cutoff},
    )
    return rows
