"""Historical Reader — 선택한 `as_of` 시점의 사실을 원장·사건으로 되살린다.

```text
Current  (지금 이 순간)     inventory_lots.remaining_qty_kg · *.status · pallets.current_location_id
                            → readmodel/current.get_current_logistics_read  (Agent Runtime 소유)
Historical (as_of 시점)     inventory_moves · pallet_events · inbound_receipts · inbound_inspections
                            → 이 파일                                 (화면 조회 소유)
```

Current Cache 를 과거 값으로 읽지 않는다. 이 파일의 존재 이유가 그것이다.

   `inventory_lots.remaining_qty_kg` 는 지금 잔량이고 과거 잔량이 아니다.
   실측(2026-09-09 · `SIM-BURNIN-202512`)에서 그 차이가 그대로 드러났다.

   ```text
   as_of        캐시 조회   원장 복원
   2026-01-05      0 kg      294.4 kg
   2026-01-09      0 kg      806.4 kg
   2026-01-17      0 kg      806.4 kg
   2026-02-05      0 kg    6,452.4 kg
   ```

   84 Lot 이 전부 잔량 0 (2026-09-12 폐기)이라 `remaining_qty_kg > 0` 조건이
   모든 과거 날짜에서 0 행을 냈다. 캐시는 지금 원장과 정확히 일치한다
   (불일치 0 건) — 틀린 것은 값이 아니라 그 값을 과거에 쓴 것이다.

Cutoff 규칙 둘. 섞지 않는다.

```text
DATE 컬럼        col <= as_of                      moved_at · received_at · arrived_at
TIMESTAMPTZ 컬럼 col <  (as_of + 1일) 00:00 KST     inspected_at · occurred_at · decided_at
```

   `occurred_at::date` 로 자르지 않는다 — 서버 timezone 에 따라 하루가 밀린다.
   `created_at` · `updated_at` · `recorded_at` 은 감사용 벽시각이라 시뮬레이션
   사실일로 쓰지 않는다.

`ADJUST` 는 계산하지 않고 멈춘다. writer 가 없고 `ADJUST_IN` / `ADJUST_OUT`
   로 갈리기 전까지 부호 계약이 없다. 방향을 넘겨짚어 그린 선은 틀렸다는 것도
   알려 주지 않는다 — 그래서 `AdjustMoveNotSupported` 로 명시적으로 실패한다.

미래 대비 함수를 미리 만들지 않는다. 그 축의 정본이 서기 전에 만들면
   지어낸 값이 된다 — 그래서 함수는 정본이 선 축에만 있다.

```text
inbound_schedule_at    W3-2   inbound_schedules 가 정본이다    → inbound_schedules.py 소유
reservation_state_at   WP-3   released_as_of 가 정본이다       → 이 파일
outbound_schedule_at   WP-3   sales · sale_items 가 정본이다  → 이 파일
```

이 파일은 읽기 조합을 맡는다(받은 연결로 SQL → 상태 유도). SQL 은
`repository/historical.py`, 유도 규칙은 `domain/historical.py`, 모델은 `schemas/historical.py`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.core.text import to_decimal
from app.logistics.domain.historical import (
    historical_lots_from_rows,
    historical_receipts_from_rows,
    historical_reservations_from_rows,
    onhand_series_from_moves,
    reject_adjust,
    reject_ambiguous_receipts,
    timestamp_cutoff,
)
from app.logistics.repository.historical import (
    select_allocation_rows_at,
    select_ledger_rows,
    select_lot_rows_at,
    select_net_moves_until,
    select_pallet_rows_at,
    select_receipt_rows_at,
    select_reservation_rows_at,
    select_runtime_coverage_rows,
    select_snapshot_day_rows,
)
from app.logistics.repository.outbound_schedules import confirmed_outbound_at
from app.logistics.schemas.historical import (
    HistoricalLot,
    HistoricalPalletPosition,
    HistoricalReceipt,
    HistoricalReservation,
    LedgerLotState,
    RuntimeSnapshotCoverage,
)
from app.logistics.schemas.snapshot import ScheduledQuantity


def ledger_state_by_lot(
    conn: Any, *, sim_run_id: str, as_of: date
) -> dict[str, LedgerLotState]:
    """`as_of` 시점의 Lot 별 원장 상태 — 잔량과 마지막 이동일.

    ```text
    balance(lot, as_of)      = Σ IN − Σ OUT − Σ DISPOSE      (moved_at <= as_of)
    last_moved_at(lot, as_of) = max(moved_at)                 (moved_at <= as_of)
    ```

    이동이 하나도 없는 Lot 은 키에 없다(0 이 아니라 «움직인 적 없음»).
    production 에서 잔량이 0 보다 큰 Lot 은 반드시 여기 있다 — Lot 은
    `remaining_qty_kg = 0` 으로 서고(`repository/inbound_stock.insert_lot`) 잔량을 올리는
    길이 원장 `IN` 하나뿐이다(`repository/ledger.update_lot_remaining` 이 유일한 writer).
    그래서 키에 없다는 것은 손으로 넣은 행이라는 뜻이고, 그때 관측일은 `None` 이다.

    :raises AdjustMoveNotSupported: 범위 안에 `ADJUST` 가 있을 때.
    """
    rows = select_ledger_rows(conn, sim_run_id=sim_run_id, as_of=as_of)
    reject_adjust(rows, sim_run_id=sim_run_id, as_of=as_of)
    return {
        row["lot_id"]: LedgerLotState(
            balance_kg=to_decimal(row["balance_kg"]),
            last_moved_at=row["last_moved_at"],
        )
        for row in rows
    }


def onhand_by_lot_at(conn: Any, *, sim_run_id: str, as_of: date) -> dict[str, Decimal]:
    """`as_of` 시점의 Lot 별 잔량. 정본은 `inventory_moves` 다.

    ```text
    balance(lot, as_of) = Σ IN − Σ OUT − Σ DISPOSE      (moved_at <= as_of)
    ```

    이동이 하나도 없는 Lot 은 키에 없다(0 이 아니라 «움직인 적 없음»).
    Lot 목록과 합칠 때 그 자리를 0 으로 읽을지 호출부가 정한다.

    `ledger_state_by_lot` 의 잔량 축만 낸다. 날짜까지 필요한 호출자는 저쪽을
    부른다 — 같은 질의를 두 벌 적지 않는다.

    :raises AdjustMoveNotSupported: 범위 안에 `ADJUST` 가 있을 때.
    """
    return {
        lot_id: state.balance_kg
        for lot_id, state in ledger_state_by_lot(
            conn, sim_run_id=sim_run_id, as_of=as_of
        ).items()
    }


def lot_state_at(conn: Any, *, sim_run_id: str, as_of: date) -> tuple[HistoricalLot, ...]:
    """`as_of` 시점에 존재한 Lot 전부 — 잔량 · 상태 · 신선도 · 회전.

    ```text
    존재      received_at <= as_of
    DISPOSED  DISPOSE Move 가 as_of 까지 있다
    DEPLETED  원장 잔량 == 0
    ACTIVE    그 외
    ```

    `inventory_lots.status` 를 읽지 않는다. 그 칸은 Current 값이고, 실측은
    84 Lot 중 77 이 `DEPLETED` · 6 이 `DISPOSED` 다 — 그대로 과거 화면에
    실으면 2026-01-05 의 살아 있던 재고가 전부 «소진» 으로 보인다.

    잔량 0 인 Lot 도 돌려준다. 걸러내는 것은 화면의 판단이지 사실이 아니다.

    보관·회전 정책은 `LEFT JOIN` 이다. `INNER JOIN` 하면 정책이 없는 품목의
    실물 재고가 조회에서 통째로 사라진다(`turnover.load_lot_turnover` 의
    같은 경고). 정책이 없다는 이유로 있는 재고를 지우지 않는다.

    :raises AdjustMoveNotSupported: 범위 안에 `ADJUST` 가 있을 때.
    """
    rows = select_lot_rows_at(conn, sim_run_id=sim_run_id, as_of=as_of)
    reject_adjust(rows, sim_run_id=sim_run_id, as_of=as_of)

    return historical_lots_from_rows(rows, as_of=as_of)


def receipt_state_at(conn: Any, *, sim_run_id: str, as_of: date) -> tuple[HistoricalReceipt, ...]:
    """`as_of` 시점의 입고 Receipt — 상태를 세 사건에서 유도한다.

    ```text
    ARRIVED       arrived_at <= as_of
    INSPECTED     검수 inspected_at < cutoff
    PUTAWAY_DONE  그 Receipt 의 Lot 과 원장 IN 이 as_of 까지 있다
    ```

    `receipt_status` 컬럼을 읽지 않는다. 실측 4건이 전부 `PUTAWAY_DONE` 인데,
    그 값을 과거 화면에 실으면 도착만 한 날에도 «입고 완료» 로 보인다.
    `inbound_receipt_events` 표를 만들지 않는 근거가 바로 사건 셋으로 4/4 가
    유도된다는 실측이다.

    한 Receipt 가 두 줄로 나오면 멈춘다. 아래 세 `LEFT JOIN` 은 전부 1:N 이
    가능한 관계다 — `inbound_inspections.receipt_id` 에도
    `inventory_lots.inbound_receipt_id` 에도 UNIQUE 가 없다(DDL 실측).
    깨지면 JOIN 곱으로 같은 Receipt 가 여러 `HistoricalReceipt` 가 되어
    화면이 도착 건수를 부풀린 채 정상으로 그린다.

       `repository/inspections.find_inspection` 이 같은 상황을 `0 / 1 / 2행 이상` 으로
       갈라 2행 이상에서 멈춘다(`InspectionIntegrityError` · "어느 것이 진짜인지
       여기서 고르지 않는다"). Historical Reader 도 같은 규율을 지킨다 —
       읽기라고 무결성 방어를 빼지 않는다.

    :raises ReceiptLineageAmbiguous: 한 `receipt_id` 가 두 줄 이상으로 돌아올 때.
    """
    rows = select_receipt_rows_at(
        conn, sim_run_id=sim_run_id, as_of=as_of, cutoff=timestamp_cutoff(as_of)
    )
    reject_ambiguous_receipts(rows, sim_run_id=sim_run_id, as_of=as_of)
    return historical_receipts_from_rows(rows, as_of=as_of)


def pallet_position_at(
    conn: Any, *, sim_run_id: str, as_of: date
) -> tuple[HistoricalPalletPosition, ...]:
    """`as_of` 시점의 Pallet 자리 — `pallet_events` 를 재생한다.

    cutoff 이전 마지막 사건이 그날의 자리다. `EMPTIED` 면 자리가 없다.

    `pallets.current_location_id` 를 과거 위치로 쓰지 않는다. 그 칸은 지금
    위치이고, 실측 3장은 전부 2026-09-12 에 `EMPTIED` 되어 지금 자리가 없다.

    사건이 하나도 없는 Pallet 은 빼고 돌려준다. 그날 그 Pallet 은 기록상
    존재하지 않았다 — 없는 자리를 지어내지 않는다. 실측 `CREATED` 3건은
    벽시각(2026-09-04)이라 2026-01 대 조회에서는 자연히 빠지고, 그 사실은
    "그때는 Pallet 기록이 없었다" 로 화면에 나가야 한다.

    실행 격리는 `pallets → inventory_lots.sim_run_id` 로 한다 — `pallet_events`
      에 `sim_run_id` 컬럼이 없다. 컬럼을 새로 만들지 않는다.
    """
    rows = select_pallet_rows_at(conn, sim_run_id=sim_run_id, cutoff=timestamp_cutoff(as_of))
    return tuple(
        HistoricalPalletPosition(
            pallet_id=row["pallet_id"],
            lot_id=row["lot_id"],
            # `EMPTIED` 는 자리를 돌려준 사건이다 — `to_location_id` 가 NULL 이다.
            location_id=row["to_location_id"],
            zone_id=row["zone_id"],
            last_event_type=row["event_type"],
            occurred_at=row["occurred_at"],
        )
        for row in rows
    )


def onhand_total_by_day(
    conn: Any, *, sim_run_id: str, start: date, end: date
) -> dict[date, Decimal]:
    """`start`~`end` 각 날 마지막 시점의 창고 전체 보유량.

    ```text
    opening(start−1)  =  Σ(moved_at <  start)
    on_hand(D)        =  on_hand(D−1) + net(D)
    ```

    현재 잔량을 앵커로 잡고 거슬러 올라가지 않는다. `remaining_qty_kg` 합을 오늘 칸에
    놓고 역산하면 그 앵커가 캐시라 모든 과거 칸이 같이 틀린다. 여기서는 시작 잔고부터
    앞으로 더한다.

    `LIMIT` 을 두지 않는다. 원장을 자른 뒤 합을 내면 잘린 줄이 하나라도 있을 때
    선 전체가 조용히 틀어진다.

    :raises AdjustMoveNotSupported: 범위 안에 `ADJUST` 가 있을 때.
    """
    rows = select_net_moves_until(conn, sim_run_id=sim_run_id, end=end)
    return onhand_series_from_moves(rows, sim_run_id=sim_run_id, start=start, end=end)


def runtime_coverage_at(conn: Any, *, sim_run_id: str, as_of: date) -> RuntimeSnapshotCoverage:
    """그날 이 실행의 Runtime Snapshot 행이 있나 — 하루를 정확히 본다.

    구간(MIN~MAX)으로 판정하지 않는다. 근거는 `RuntimeSnapshotCoverage`
    docstring 의 실측 세 줄이다 (구간 안 미개장 31일 · 사실 없는 정상일 245일).

    함께 돌려주는 `first_as_of` · `last_as_of` 는 화면 문구용 맥락이다 —
    "2028-01-17 은 이 실행에 없다" 만 적으면 어디를 물어야 할지 알 수 없다.
    """
    rows = select_runtime_coverage_rows(conn, sim_run_id=sim_run_id, as_of=as_of)
    row = rows[0] if rows else {}
    return RuntimeSnapshotCoverage(
        as_of=as_of,
        # 행이 하나도 없으면 `bool_or` 가 NULL 이다 — `None` 을 참으로 읽지 않는다.
        has_snapshot=bool(row.get("has_snapshot")),
        first_as_of=row.get("first_as_of"),
        last_as_of=row.get("last_as_of"),
    )


def snapshot_days_between(
    conn: Any, *, sim_run_id: str, start: date, end: date
) -> frozenset[date]:
    """`start`~`end` 중 Runtime Snapshot 이 실제로 있는 날들.

    그래프의 칸마다 그날을 따로 물어야 한다. 원장 누계는 어떤 날짜에도 숫자를
    내고, 첫 사실 이전 구간에서는 그 숫자가 0 이다. 그 0 은 "확인했고 재고가
    없다" 가 아니라 "그날을 모른다" 인데, 화면 계약에서 그 둘은 다른 값이다 —
    안 가르면 안 연 날이 «재고 0kg» 선으로 그려진다.

    창이 12칸이라 한 질의로 집합을 받아 온다 — 칸마다 묻지 않는다.
    """
    rows = select_snapshot_day_rows(conn, sim_run_id=sim_run_id, start=start, end=end)
    return frozenset(row["as_of"] for row in rows)


# ── 출고 축 (WP-3) ──────────────────────────────────────────────────────


def reservation_state_at(
    conn: Any, *, sim_run_id: str, as_of: date
) -> tuple[HistoricalReservation, ...]:
    """`as_of` 시점의 예약과 그 아래 할당 — 저장된 `status` 를 안 읽는다.

    ```text
    존재    sales.order_date <= as_of     ← 판매가 확정된 날부터다 (납품일이 아니다)
    소멸    released_as_of <= as_of        그날부터 놓아준 것이다
    진행도  할당(decided_at) · 원장 OUT(moved_at) 에서 유도
    ```

    `sales.order_date` 로 존재를 자른다. `sim_run_id` 로만 고르면 미래 납품 예약이
    과거 조회에 그대로 나온다(2026-01-20 납품 예약이 2026-01-10 화면에 나온다).
    그리고 예약이 서는 날은 납품일이 아니라 확정일이다 — 마스터
    `service/sales_approval.confirm_approved_sale` 이 확정 직후 같은 커밋으로
    `reserve_confirmed_sale_available` 을 부른다. `order_date` 가 그 확정 실행의
    `as_of` 그 자체다(`order_date=as_of`).

       `sale_date` 로 자르면 확정일 D 에 선 예약이 D 화면에서 사라진다. 그날
       Runtime 은 이미 그 몫을 잡고 있는데(`service/outbound.item_free_stock_qty` 의
       미할당 예약) Historical 만 하루 늦어 — 같은 화면의 예약 목록과 판매가능량이
       서로 다른 날을 가리킨다.

       실측(2026-09-15 · 실 DB · `master_day_openings` 개장 벽시각 사이 판정):
       2026-09-12 이후 걷기 1,552행이 전부 확정일 생성이다. 그 이전 걷기
       (`SIM-CHAIN-V3` · `V4` 40행)만 납품일에 섰고 이 규칙으로는 하루 이르게 보인다 —
       되살릴 근거가 없어 그대로 둔다(`shown_run` 이 가리키는 실행이 아니다).

       `reserved_as_of` 같은 칸을 새로 만들지 않는다. 기존 행은 Backfill 금지라
       `NULL` 이 되고 결국 `order_date` 로 유도해야 한다 — 새 칸이 이 규칙보다
       정확해지는 실행이 없다. 예약 생성일이 `order_date` 와 갈리는 날 다시 본다.

       «그날 몇 kg 이었나» 는 이것이 안 고친다. 존재 날짜와 확보량은 다른 축이고,
       확보량 쪽 한계는 `HistoricalReservation.reserved_qty_kg` 에 적어 뒀다.

       `sale_id` 가 `NULL` 인 예약은 안 낸다. 그 행에는 존재일을 댈 근거가
       하나도 없다 — `created_at` 은 벽시각이라 못 쓰고, 없는 날짜를 지어내면
       그 예약이 아무 날에나 나타난다. 실측(2026-09-09) 8행 전부 `sale_id` 가
       있어 빠지는 행은 0건이었고, production 에서 그 값을 비우는 경로도
       없다(예약을 세우는 유일한 문이 판매 봉투를 받는다). 스키마상 가능한
       상태라 규칙만 적어 둔다.

    `inventory_reservations.status` 도 `inventory_allocations.status` 도
    과거 정본이 아니다. 둘 다 지금 값이라, 오늘 놓아준 예약이 그 예약이
    살아 있던 과거 날짜에도 `RELEASED` · `CANCELLED` 로 보인다.

       딱 한 자리 예외 — `released_as_of <= as_of` 인 날의 `RELEASED` /
       `CANCELLED` 구분이다. 놓아준 뒤에는 그 칸을 바꾸는 경로가 없어 저장된 값이
       곧 놓아주던 날의 값이다. 그 전 날짜로는 역류시키지 않는다.

    ```text
    할당 존재    decided_at < timestamp_cutoff(as_of)
    SHIPPED      MOVE-OUT-{allocation_id} · move_type='OUT' · moved_at <= as_of
    RELEASED     그 예약의 released_as_of <= as_of   ← 놓아주면 할당도 함께 내려간다
    ALLOCATED    그 밖
    ```

       같은 판 안의 취소는 신경 쓰지 않는다. production 에서 할당이 취소되는
       길은 둘뿐이고(`release_reservation` · FEFO 재적합) 앞엣것은
       `released_as_of` 로 유도되며 뒤엣것은 같은 날 안이다 — 되살리기 날짜
       경계(`domain/outbound.assert_revivable_on_same_day`)가 그것을 하루로 묶는다.
       그래서 `allocation_cancelled_as_of` 같은 칸을 새로 만들지 않는다.

    축은 `(sim_run_id, as_of)` 다. 실행을 안 좁히면 남의 실행 예약이 섞인다.
    """
    cutoff = timestamp_cutoff(as_of)
    rows = select_reservation_rows_at(conn, sim_run_id=sim_run_id, as_of=as_of)
    if not rows:
        return ()

    할당들 = select_allocation_rows_at(
        conn, reservation_ids=[row["reservation_id"] for row in rows],
        as_of=as_of, cutoff=cutoff,
    )

    return historical_reservations_from_rows(rows, 할당들, as_of=as_of)


def outbound_schedule_at(
    conn: Any, *, sim_run_id: str, as_of: date
) -> tuple[ScheduledQuantity, ...]:
    """`as_of` 에서 보였어야 하는 미래 확정 출고를 판매 정본에서 되살린다.

    `confirmed_outbound_json` 을 안 읽는다(WP-3). 그 칸은 판매 확정이 채우는
    경로가 하나도 없어 실측 254행 전부 `[]` 였다 — 비어 있는 칸을 과거 화면에
    실으면 "그날 미래 출고가 없었다" 가 확인된 사실처럼 나간다.

    ```text
    원천   sales · sale_items                    ← Current 축과 같은 정본
    축     sim_run_id · sale_date > as_of
    상태   order_status IN (CONFIRMED, READY)
    ```

    주의: `sales.order_status` 는 지금 값이다. 그래서 이 함수는 "그날 그 판매가
    확정 상태였나" 를 정확히는 못 답한다 — 판매 상태의 시뮬레이션 날짜 칸이
    없어서다. 그 한계를 숨기지 않는다: 지금 취소된 판매는 과거 화면에서도
    안 보이고, 지금 배송된 판매는 그 납품일 이전 화면에서 미래 출고로 안 선다.

       그럼에도 fixture JSON 보다 낫다. 저쪽은 아무도 안 쓰는 빈 칸이라 늘
       «0 건» 이고, 이쪽은 적어도 실재하는 판매 사실을 축으로 삼는다. 판매 상태
       이력 표는 판매 소유라 물류가 만들지 않는다(파트 경계).

    Current 경로와 같은 함수를 쓴다(`outbound_schedules.confirmed_outbound_at`).
    두 벌로 적으면 화면과 Runtime 이 다른 미래를 그린다.
    """
    return tuple(confirmed_outbound_at(conn, sim_run_id=sim_run_id, as_of=as_of))
