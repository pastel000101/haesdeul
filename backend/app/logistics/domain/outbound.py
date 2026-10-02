"""출고의 판정 · ID 규칙 — 할당 ID · Move ID · 판매 가용 거르기 · 예약 가능량 · 예약 사실 대조 ·
되살리기 날짜 경계 · 할당 진행도 → 예약 상태.

DB 를 만지지 않는다 — 시간대(`SEOUL`)는 상수로만 읽고 시계를 읽지 않는다. 업무 순서는
`service/outbound.py` 에 있다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.core.clock import SEOUL
from app.logistics.domain.turnover import freshness_days_of, is_disposal_candidate
from app.logistics.schemas.outbound import (
    ALLOCATION_BASES,
    AllocationBasis,
    AllocationRequest,
    InvalidOutboundRequest,
    OutboundIntegrityError,
    ReservationConflict,
    ReservationStatus,
)

# ── 순수 도우미 ─────────────────────────────────────────────────────────


def allocation_id_for(*, reservation_id: str, lot_id: str) -> str:
    """할당 PK. 순수 계산이고 결정론이다.

    ```text
    ALC-{reservation_id}-{lot_id}
    ```

    한 예약이 여러 Lot 에 걸칠 수 있고(스키마가 그렇게 설계됐다) Lot 마다 한 줄
    이므로, 두 값이 함께여야 정체성이 된다.

    난수 · 시계 · 시퀀스를 쓰지 않는다 — 재실행이 같은 할당을 다른 행으로 만들면
    가용량이 두 번 깎인다.
    """
    outbound_text(reservation_id, 칸="reservation_id")
    outbound_text(lot_id, 칸="lot_id")
    return f"ALC-{reservation_id}-{lot_id}"


def move_id_for_allocation(*, allocation_id: str) -> str:
    """출고 Move 의 멱등 키. 할당에 뿌리를 둔다.

    `service/ledger.record_inventory_move` 가 `move_id` 로 이미 멱등하다 — 그 장치를 그대로
    쓴다.
    """
    outbound_text(allocation_id, 칸="allocation_id")
    return f"MOVE-OUT-{allocation_id}"


def outbound_text(값: Any, *, 칸: str) -> str:
    if not isinstance(값, str) or not 값.strip():
        raise InvalidOutboundRequest(f"{칸} 가 비었다: {값!r}")
    return 값


def outbound_quantity(값: Any, *, 칸: str) -> Decimal:
    """수량을 `Decimal` 로 좁힌다. float 도 비유한값도 받지 않는다.

    `ledger.ledger_quantity` · `inspections.inspection_quantity` 와 같은 규율이다.
    """
    if isinstance(값, bool) or not isinstance(값, Decimal):
        raise InvalidOutboundRequest(
            f"{칸} 은 Decimal 이어야 한다 (받은 것: {값!r} · {type(값).__name__})."
        )
    if not 값.is_finite():
        raise InvalidOutboundRequest(f"{칸} 이 유한한 수가 아니다: {값!r}")
    if 값 <= 0:
        raise InvalidOutboundRequest(f"{칸} 은 0보다 커야 한다 (받은 것: {값})")
    return 값


def available_qty(행: Mapping[str, Any]) -> Decimal:
    return Decimal(행["remaining_qty_kg"]) - Decimal(행["held_qty_kg"])


def assert_same_reservation_facts(
    기존: Mapping[str, Any],
    *,
    reservation_id: str,
    sim_run_id: str,
    item_id: str,
    sale_id: str | None,
    required: Decimal,
    due_date: date | None,
) -> None:
    """같은 `reservation_id` 에 다른 사실이 있으면 멈춘다.

    `service/outbound` 의 `reserve_stock` 과 `reserve_available_stock` 이 같은 규칙을 써야
    한다 — 두 문이 다른 눈으로 충돌을 보면 어느 문으로 들어왔느냐가 사실을 바꾼다.

    `reserved_qty_kg` 는 여기서 안 본다. 그 값은 재실행마다 커지는 것이라
    "다른 사실" 이 아니다. 요구량·품목·판매·납기가 정체성이다.
    """
    다른것 = {
        칸: (기존[칸], 값)
        for 칸, 값 in (
            ("sim_run_id", sim_run_id),
            ("item_id", item_id),
            ("sale_id", sale_id),
            ("required_qty_kg", required),
            ("due_date", due_date),
        )
        if 기존[칸] != 값
    }
    if 다른것:
        raise ReservationConflict(
            f"같은 reservation_id 에 다른 사실의 예약이 있다"
            f" ({reservation_id!r}): {다른것!r}. 덮지도 버리지도 않는다."
        )


def sim_day(moment: datetime) -> date:
    """시각 하나를 시뮬레이션 달력의 하루로 옮긴다.

    `::date` 도 `.date()` 도 그냥 쓰지 않는다. 서버 timezone 에 따라 하루가
    밀린다 — `domain/historical.timestamp_cutoff` 가 같은 이유로 KST 를 박는다.

    tz 없는 값은 안 받는다. `allocate_stock` 이 이미 naive `decided_at` 을 거부하고,
    DB 컬럼도 `TIMESTAMPTZ` 라 여기 오는 값에는 늘 시간대가 있다.
    """
    if moment.tzinfo is None:
        raise InvalidOutboundRequest(
            f"시간대 없는 시각으로 시뮬레이션 날짜를 만들 수 없다: {moment!r}."
        )
    return moment.astimezone(SEOUL).date()


def assert_revivable_on_same_day(
    되살릴것: Mapping[str, Any], *, allocation_id: str, decided_at: datetime
) -> None:
    """취소된 할당을 같은 시뮬레이션 날짜 안에서만 다시 세운다 (WP-3).

    ```text
    같은 날      되살린다      그날 안의 재적합이다 (FEFO 가 내리고 다시 세운다)
    날짜를 넘김  막는다        그날 취소였던 사실이 소급해 사라진다
    ```

    날짜를 넘기면 안 되는 이유: 되살리기는 같은 행의 `decided_at` 을 새 값으로
    덮는다. D 에 취소하고 D+1 에 되살리면 그 행은 "D+1 에 결정된 살아 있는 할당"
    이 되고, D 시점 조회가 그 할당을 못 본다 — 그날 실제로 취소 상태였다는
    사실이 아무 기록 없이 사라진다. 새 정체성으로 세우는 것도 아니라
    `MOVE-OUT-{allocation_id}` 까지 같은 이름을 쓴다.

    `created_at` 으로 재지 않는다. 그것은 벽시각이라 DB 를 손본 시각이지
    시뮬레이션 날짜가 아니다 (`released_as_of` 를 만든 것과 같은 이유다).

    되살릴 자리가 아니면(취소된 행이 아니면) 아무 말도 안 한다 — 이 함수를 부르는
    자리가 이미 `CANCELLED` 만 통과시킨다.

    :raises OutboundIntegrityError: 취소된 날과 다른 날에 되살리려 할 때.
    """
    이전 = 되살릴것.get("decided_at")
    if not isinstance(이전, datetime):
        # 잴 근거가 없으면 통과시키지 않는다. 날짜를 모르는 채 되살리는 것은
        # 경계가 없는 것과 같다.
        raise OutboundIntegrityError(
            f"되살릴 할당의 decided_at 을 읽을 수 없다 ({allocation_id!r}): {이전!r}."
            " 언제 정해진 할당인지 모르면 같은 날인지 가릴 수 없다."
        )
    이전날 = sim_day(이전)
    이번날 = sim_day(decided_at)
    if 이전날 != 이번날:
        raise OutboundIntegrityError(
            f"취소된 할당을 다른 날에 되살릴 수 없다 ({allocation_id!r}):"
            f" 취소된 날 {이전날} · 이번 {이번날}."
            " 되살리면 그날 취소였다는 사실이 소급해 사라진다 —"
            " 다른 날 몫은 새 예약으로 낸다."
        )


def reservation_status_for(예약: Mapping[str, Any], *, allocated: Decimal) -> ReservationStatus:
    """할당 진행도로 예약 상태를 정한다. 어휘는 DB 것 그대로다.

    기준은 `required_qty_kg` 다. 부분 예약에서도 안 바꾼다.

    ```text
    required 100 · reserved 60 · allocated 60
    요구량 기준   PARTIALLY_ALLOCATED   맞다    이 판매는 아직 다 못 냈다
    확보량 기준   ALLOCATED             틀리다  60 만 내고 "다 됐다" 로 보인다
    ```

    상한(`allocate_stock`)은 확보량이고 상태는 요구량인 것이 어긋나 보이지만, 둘은
    다른 질문에 답한다 — "얼마까지 붙일 수 있나" 와 "이 판매가 다 나갔나" 다.

    가용량 계산에는 영향이 없다. 셋(`RESERVED` · `PARTIALLY_ALLOCATED` ·
    `ALLOCATED`)이 전부 `HOLDING_RESERVATION` 이라 어느 쪽으로 앉든 잡힌 몫은 같다.
    """
    if allocated <= 0:
        return "RESERVED"
    if allocated >= Decimal(예약["required_qty_kg"]):
        return "ALLOCATED"
    return "PARTIALLY_ALLOCATED"


def sellable_lot_rows(행들: list[dict[str, Any]], *, as_of: date) -> list[dict[str, Any]]:
    """판매 가용에서 이미 빠진 Lot(신선도 소진 · 폐기대기)을 거른다. DB 를 만지지 않는다.

    `0 != null` — 신선도를 모르는 Lot 은 빼지 않는다 (확인된 만료가 아니다).
    """
    return [
        행
        for 행 in 행들
        if not is_disposal_candidate(remaining_freshness_days=freshness_days_of(행, as_of=as_of))
    ]


def free_stock_qty(*, 가용합: Decimal, 미할당: Decimal, item_id: str) -> Decimal:
    """판매가능 Lot 가용량 합 − 미할당 예약.

    음수는 0 으로 보정하지 않고 멈춘다. DB 를 만지지 않는다.
    """
    free = 가용합 - 미할당
    if free < 0:
        raise OutboundIntegrityError(
            f"예약 가능량이 음수다 (item_id={item_id!r}): 판매가능 {가용합}"
            f" · 미할당 예약 {미할당}."
            " 0 으로 보정하지 않는다 — 잡힌 몫이 실재 재고를 넘었다는 뜻이다."
        )
    return free


def check_allocation_request(
    *,
    reservation_id: str,
    decided_by: str,
    decided_at: datetime,
    allocation_basis: AllocationBasis,
    requests: Sequence[AllocationRequest],
) -> None:
    """할당 요청의 모양 — 누가·언제(tz)·어떤 근거로·무엇을. DB 를 만지지 않는다."""
    outbound_text(reservation_id, 칸="reservation_id")
    outbound_text(decided_by, 칸="decided_by")
    if not isinstance(decided_at, datetime) or decided_at.tzinfo is None:
        raise InvalidOutboundRequest(
            f"decided_at 은 시간대를 단 datetime 이어야 한다: {decided_at!r}"
        )
    if allocation_basis not in ALLOCATION_BASES:
        raise InvalidOutboundRequest(
            f"할당 근거가 계약 어휘 밖이다: {allocation_basis!r}."
            f" 허용: {sorted(ALLOCATION_BASES)}."
        )
    if not requests:
        raise InvalidOutboundRequest("할당할 Lot 이 하나도 없다.")


def bundle_allocation_requests(requests: Sequence[AllocationRequest]) -> dict[str, Decimal]:
    """Lot → 이번 할당량. 같은 Lot 이 두 번 오면 멈춘다(합쳐서 한 번에 준다).

    DB 를 만지지 않는다.
    """
    묶음: dict[str, Decimal] = {}
    for 요청 in requests:
        lot_id = outbound_text(요청.lot_id, 칸="lot_id")
        수량 = outbound_quantity(요청.quantity_kg, 칸="quantity_kg")
        if lot_id in 묶음:
            raise InvalidOutboundRequest(
                f"같은 Lot 이 요청에 두 번 있다: {lot_id!r}. 합쳐서 한 번에 준다."
            )
        묶음[lot_id] = 수량
    return 묶음
