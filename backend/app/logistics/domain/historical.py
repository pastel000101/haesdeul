"""과거 시점 상태 유도 — 기준 시각 · ADJUST 거부 · Lot/Receipt/할당/예약 상태 · 보유량 누계.

★ 2026-09-30 재구성 BL-015: `logistics/historical_repository.py` 에서 판단 · 조립 부분을 옮겼다. DB
  를 만지지 않는다 —
  KST 자정 기준(`timestamp_cutoff`)은 시간대 상수만 쓴다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from app.core.clock import SEOUL
from app.core.text import to_decimal
from app.logistics.domain.grade import normalize_grade
from app.logistics.domain.outbound import reservation_status_for
from app.logistics.domain.turnover import lot_turnover_from_row
from app.logistics.schemas.historical import (
    AdjustMoveNotSupported,
    HistoricalAllocation,
    HistoricalAllocationState,
    HistoricalCapacity,
    HistoricalLot,
    HistoricalLotState,
    HistoricalReceipt,
    HistoricalReceiptState,
    HistoricalReservation,
    HistoricalReservationState,
    ReceiptLineageAmbiguous,
)


def timestamp_cutoff(as_of: date) -> datetime:
    """TIMESTAMPTZ 컬럼용 상한. **`< cutoff` 로 쓴다 (`<=` 아니다).**

    ```text
    cutoff = (as_of + 1 달력일) 00:00 Asia/Seoul
    ```

    🔴 **`col::date <= as_of` 로 쓰지 않는다.** 그 비교는 서버 `TimeZone` 설정에
       따라 하루가 밀리고, 밀린 것을 아무도 알아채지 못한다. tz-aware 파라미터를
       넘겨 DB 가 같은 순간을 보게 한다.
    """
    return datetime.combine(as_of + timedelta(days=1), time.min, tzinfo=SEOUL)


def reject_adjust(rows: list[dict[str, Any]], *, sim_run_id: str, as_of: date) -> None:
    lots = [row["lot_id"] for row in rows if int(row.get("adjust_count") or 0) > 0]
    if lots:
        raise AdjustMoveNotSupported(
            "방향을 모르는 ADJUST 이동이 있어 과거 잔량을 계산하지 않는다 "
            f"(sim_run_id={sim_run_id!r} · as_of={as_of} · lot_id={sorted(lots)})."
        )


def lot_state(
    *, balance: Decimal, dispose_on_last_day: int, out_on_last_day: int
) -> HistoricalLotState:
    """Lot 상태를 **쓰는 쪽 규칙 그대로** 되살린다.

    ```text
    잔량 > 0                                 ACTIVE
    잔량 0 · 비운 날에 DISPOSE 있고 OUT 없음   DISPOSED
    그 외 (잔량 0)                            DEPLETED
    ```

    🔴 **«폐기 이동이 있었나» 로 갈라서는 안 된다.** `disposal._mark_disposed` 는
       **그 DISPOSE 가 잔량을 0 으로 만들었을 때만** `DISPOSED` 를 적는다 —
       *"부분 폐기에는 붙이지 않는다"* 가 그 함수 첫 줄이고, `WHERE … AND
       remaining_qty_kg = 0` 이 SQL 로도 그것을 막는다. 총 폐기량이 0 보다 크다는
       것만 보면 **30kg 만 버리고 70kg 는 정상 출고한 Lot** 이 «전량 폐기» 로 둔갑한다.

    ```text
    실측 (SIM-BURNIN-202512 · 2026-09-09)
      DISPOSE 이동이 있는 Lot        8
        DB status = DISPOSED         6
        DB status = DEPLETED         2   ← 부분 폐기 뒤 OUT 으로 소진됐다
    ```

       종전 판정은 그 2건을 `DISPOSED` 로 냈다. 위 규칙은 8/8 을 DB 와 같게 낸다.

    ⚠️ **같은 날 OUT 과 DISPOSE 가 함께 있으면 `DISPOSED` 라고 하지 않는다.**
       `inventory_moves.moved_at` 은 DATE 라 하루 안의 순서가 없어(실측: 그런 Lot 2건)
       어느 쪽이 잔량을 0 으로 만들었는지 증명할 수 없다. 증명 안 되는 «폐기» 를
       적기보다 *"비었다"* 까지만 말한다 — 모르는 것을 아는 척하지 않는 그 규율이다.
    """
    if balance > 0:
        return "ACTIVE"
    if dispose_on_last_day > 0 and out_on_last_day == 0:
        return "DISPOSED"
    return "DEPLETED"


def reject_ambiguous_receipts(
    rows: list[dict[str, Any]], *, sim_run_id: str, as_of: date
) -> None:
    """한 `receipt_id` 가 두 줄 이상이면 멈춘다. **하나를 고르지 않는다.**

    ★ 세 JOIN(검수 · Lot · 원장 IN) 중 **어디서** 늘었는지까지 적는다 — 그래야
      고칠 사람이 어느 표를 볼지 안다.
    """
    seen: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        seen.setdefault(row["receipt_id"], []).append(row)
    겹친것 = {receipt_id: found for receipt_id, found in seen.items() if len(found) > 1}
    if not 겹친것:
        return
    상세 = []
    for receipt_id, found in sorted(겹친것.items()):
        상세.append(
            f"{receipt_id}: {len(found)}줄"
            f" (inspection {sorted({r['inspection_id'] for r in found})!r}"
            f" · lot {sorted({r['lot_id'] for r in found})!r}"
            f" · in_move {sorted({r['in_move_id'] for r in found})!r})"
        )
    raise ReceiptLineageAmbiguous(
        "한 Receipt 에 검수·Lot·원장 IN 이 둘 이상 붙어 있다"
        f" (sim_run_id={sim_run_id!r} · as_of={as_of}): {' / '.join(상세)}."
        " 어느 것이 진짜인지 여기서 고르지 않는다 —"
        " 앞 행이 뒤 행을 덮게 두는 것도 고르는 것이다."
    )


def receipt_state(row: dict[str, Any]) -> HistoricalReceiptState:
    if row["lot_id"] is not None and row["in_move_id"] is not None:
        return "PUTAWAY_DONE"
    if row["inspection_id"] is not None:
        return "INSPECTED"
    return "ARRIVED"


def capacity_at(
    *,
    used_capacity_kg: Decimal,
    guaranteed_capacity_kg: Decimal | None,
    burst_capacity_kg: Decimal | None,
) -> HistoricalCapacity:
    """창고 kg Capacity 한 벌. 🔴 **한계를 `capacity_basis` 로 말한다.**

    `agent_policy_config` 에 유효일 컬럼이 없어 «그날 그 정책이었나» 를 알 수 없다.
    유효일 컬럼을 새로 만드는 것은 이번 범위가 아니므로(`07 §15`), 지금 활성
    정책을 쓰되 **그 사실을 응답에 적는다.** 조용히 과거 값인 척하지 않는다.

    ★ `used_capacity_kg` 는 호출부가 넘긴 **그 시점 원장 합**이다 — 캐시 합이 아니다.
    """
    return HistoricalCapacity(
        used_capacity_kg=used_capacity_kg,
        guaranteed_capacity_kg=guaranteed_capacity_kg,
        burst_capacity_kg=burst_capacity_kg,
    )


def historical_allocation(
    row: dict[str, Any], *, reservation_state: HistoricalReservationState
) -> HistoricalAllocation:
    """할당 한 줄의 그날 상태를 유도한다. **순서가 계약이다.**

    ```text
    ① 원장 OUT 이 있으면        SHIPPED    나간 것은 되돌릴 수 없다
    ② 예약이 놓아준 뒤면        RELEASED   놓아주면 아직 안 나간 할당도 함께 내려간다
    ③ 그 밖                     ALLOCATED
    ```

    🔴 **①이 ②보다 먼저다.** `release_reservation` 은 `SHIPPED` 할당이 하나라도 있으면
       멈추므로 둘이 함께 참일 수 없지만, 순서를 뒤집으면 그 불변식이 깨지는 날
       **이미 나간 재고가 «놓아줬다» 로 보인다.**
    """
    shipped_at = row["shipped_at"]
    if shipped_at is not None:
        state: HistoricalAllocationState = "SHIPPED"
    elif reservation_state == "RELEASED":
        state = "RELEASED"
    else:
        state = "ALLOCATED"
    return HistoricalAllocation(
        allocation_id=row["allocation_id"],
        reservation_id=row["reservation_id"],
        lot_id=row["lot_id"],
        pallet_id=row["pallet_id"],
        allocated_qty_kg=to_decimal(row["allocated_qty_kg"]),
        allocation_basis=row["allocation_basis"],
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
        state=state,
        shipped_at=shipped_at if state == "SHIPPED" else None,
        note=row["note"],
    )


def reservation_status_at(
    row: dict[str, Any], *, state: HistoricalReservationState, assigned: Decimal
) -> str:
    """그날의 `ReservationStatus` 를 유도한다. **DB 어휘를 그대로 쓴다.**

    ```text
    RELEASED    저장된 status (RELEASED / CANCELLED)   ← 놓아준 날부터만
    HOLDING     reservation_status_for(할당 진행도)    ← 새 어휘를 안 만든다
    ```

    🔴 **살아 있던 날에는 저장된 값을 안 본다.** 오늘 `RELEASED` 인 예약도 놓아주기
       전날에는 `RESERVED` · `PARTIALLY_ALLOCATED` · `ALLOCATED` 중 하나였다.

    ★ **진행도 식을 여기서 다시 적지 않는다.** `outbound._reservation_status_for` 가
      그 규칙의 주인이고(`required_qty_kg` 기준 · 확보량 기준 아님), 두 벌로 적으면
      한쪽만 고쳐지는 날이 온다.
    """
    if state == "RELEASED":
        # ★ 놓아준 뒤에는 그 칸을 바꾸는 경로가 없어, 저장된 값이 놓아주던 날의 값이다.
        저장 = row["stored_status"]
        return 저장 if 저장 in ("RELEASED", "CANCELLED") else "RELEASED"
    return reservation_status_for(row, allocated=assigned)


def reservation_state_of(
    released_as_of: date | None, *, as_of: date
) -> HistoricalReservationState:
    """`released_as_of` 한 칸으로 그날 상태를 가른다. **벽시각을 안 본다.**

    ★ `released_as_of == as_of` 는 **RELEASED** 다 — 그날부터 놓아준 것이다
      (`inbound_schedules.cancelled_as_of` 와 같은 경계다).
    """
    if released_as_of is None or released_as_of > as_of:
        return "HOLDING"
    return "RELEASED"


def historical_lots_from_rows(
    rows: list[dict[str, Any]], *, as_of: date
) -> tuple[HistoricalLot, ...]:
    """읽은 Lot 행 → 그날의 Lot (잔량 · 유도 상태 · 정규화 등급). DB 를 만지지 않는다."""
    lots: list[HistoricalLot] = []
    for row in rows:
        balance = to_decimal(row["balance_kg"])
        lots.append(
            HistoricalLot(
                lot_id=row["lot_id"],
                item_id=row["item_id"],
                item_name=row["item_name"],
                grade=normalize_grade(row["grade"]),
                storage_zone=row["storage_zone"],
                received_at=row["received_at"],
                unit_cost_krw_per_kg=row["unit_cost_krw_per_kg"],
                remaining_qty_kg=balance,
                state=lot_state(
                    balance=balance,
                    dispose_on_last_day=int(row["dispose_on_last_day"] or 0),
                    out_on_last_day=int(row["out_on_last_day"] or 0),
                ),
                # ★ `turnover` 가 쓰는 그 함수에 **그 시점 잔량**만 바꿔 넣는다.
                #   신선도·회전 공식을 여기서 다시 적으면 두 답이 갈린다.
                turnover=lot_turnover_from_row({**row, "remaining_qty_kg": balance}, as_of=as_of),
            )
        )
    return tuple(lots)


def historical_receipts_from_rows(
    rows: list[dict[str, Any]], *, as_of: date
) -> tuple[HistoricalReceipt, ...]:
    """읽은 Receipt 계보 행 → 그날의 Receipt (유도 상태). DB 를 만지지 않는다."""
    return tuple(
        HistoricalReceipt(
            receipt_id=row["receipt_id"],
            inbound_id=row["inbound_id"],
            item_id=row["item_id"],
            item_name=row["item_name"],
            arrived_at=row["arrived_at"],
            ordered_qty_kg=row["ordered_qty_kg"],
            accepted_qty_kg=row["accepted_qty_kg"],
            hold_qty_kg=row["hold_qty_kg"],
            rejected_qty_kg=row["rejected_qty_kg"],
            fact_source=row["fact_source"],
            state=receipt_state(row),
            inspection_id=row["inspection_id"],
            inspection_verdict=row["inspection_verdict"],
            inspected_qty_kg=row["inspected_qty_kg"],
            lot_id=row["lot_id"],
            in_move_id=row["in_move_id"],
        )
        for row in rows
    )


def onhand_series_from_moves(
    rows: list[dict[str, Any]], *, sim_run_id: str, start: date, end: date
) -> dict[date, Decimal]:
    """날짜별 순증감 행 → `start..end` 칸마다의 보유량(원장 누계). ADJUST 가 있으면 멈춘다.

    DB 를 만지지 않는다.
    """
    adjust_days = [row["moved_at"] for row in rows if int(row["adjust_count"] or 0) > 0]
    if adjust_days:
        raise AdjustMoveNotSupported(
            "방향을 모르는 ADJUST 이동이 있어 재고 추이를 계산하지 않는다 "
            f"(sim_run_id={sim_run_id!r} · moved_at={sorted(adjust_days)})."
        )

    net_by_day = {row["moved_at"]: to_decimal(row["net_kg"]) for row in rows}
    running = sum((qty for day, qty in net_by_day.items() if day < start), Decimal(0))

    series: dict[date, Decimal] = {}
    day = start
    while day <= end:
        running += net_by_day.get(day, Decimal(0))
        series[day] = running
        day += timedelta(days=1)
    return series


def historical_reservations_from_rows(
    rows: list[dict[str, Any]],
    할당들: Mapping[str, tuple[dict[str, Any], ...]],
    *,
    as_of: date,
) -> tuple[HistoricalReservation, ...]:
    """읽은 예약·할당 행 → 그날의 예약 (유도 상태 · 진행도 · 미할당). DB 를 만지지 않는다."""
    지은것: list[HistoricalReservation] = []
    for row in rows:
        released = row["released_as_of"]
        상태 = reservation_state_of(released, as_of=as_of)
        할당 = tuple(
            historical_allocation(할당행, reservation_state=상태)
            for 할당행 in 할당들.get(row["reservation_id"], ())
        )
        나간것 = sum(
            (a.allocated_qty_kg for a in 할당 if a.state == "SHIPPED"), start=Decimal(0)
        )
        잡은것 = sum(
            (a.allocated_qty_kg for a in 할당 if a.state == "ALLOCATED"), start=Decimal(0)
        )
        확보 = to_decimal(row["reserved_qty_kg"])
        # 🔴 **놓아준 날부터 «아직 안 고른 몫» 은 0 이다 (WP-3 보정 2).** 확보량은
        #    보존되므로 그대로 빼면 놓아준 예약이 *"아직 60kg 남았다"* 로 보인다.
        미할당 = (
            Decimal(0) if 상태 == "RELEASED"
            else max(Decimal(0), 확보 - (잡은것 + 나간것))
        )
        지은것.append(
            HistoricalReservation(
                reservation_id=row["reservation_id"],
                sim_run_id=row["sim_run_id"],
                item_id=row["item_id"],
                item_name=row["item_name"],
                sale_id=row["sale_id"],
                sale_date=row["sale_date"],
                required_qty_kg=to_decimal(row["required_qty_kg"]),
                reserved_qty_kg=확보,
                due_date=row["due_date"],
                state=상태,
                status=reservation_status_at(
                    row, state=상태, assigned=잡은것 + 나간것
                ),
                released_as_of=released,
                allocated_qty_kg=잡은것,
                shipped_qty_kg=나간것,
                unallocated_qty_kg=미할당,
                allocations=할당,
            )
        )
    return tuple(지은것)
