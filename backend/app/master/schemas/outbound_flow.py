"""출고 단계 모델 — 그날 나갈 판매 줄, 줄별 결과, 단계 결과."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

# ── ① 그날 나갈 것 ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class DueSaleItem:
    """그날 나가야 하는 판매 품목 한 줄. 판매가 소유한 사실을 읽어 온 것뿐이다.

    `sale_date` 를 들고 다니는 것은 저장하려는 것이 아니라 비교하려는 것이다. 정본은
    `sales.sale_date` 이고, 이 값은 그 행에서 방금 읽은 사본이다.
    """

    sale_id: str
    sale_item_id: str
    item_id: str
    sim_run_id: str
    quantity_kg: Decimal
    sale_date: date


# ── ② 결과 어휘 ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SaleItemOutcome:
    """판매 품목 하나의 출고 결과. 실패한 품목도 예외가 아니라 이 값으로 남는다.

    `RAN` 은 예약 → 할당 → 출고가 끝까지 돌았다는 뜻이고, 전량이 나갔다는 뜻은 아니다.
    부분 예약이면 확보된 만큼만 나가고 그것이 정상이다. 실제로 나간 양은
    `shipped_qty_kg`, 필요했던 양은 `required_qty_kg` 가 나른다.

    `SHORT` 는 `FAILED` 가 아니다. 예를 들어 `required=100, reserved=0` 은 확보가 0kg 이라
    나간 것이 없다는 정상 사업 결과(부족)이지 오류가 아니다.

    `SHORT` 는 날 단위 값인 `NOTHING_DUE`("그날 나갈 판매가 없다")와도 다르다. 나갈
    판매가 있었는데 확보가 0이었다는 뜻이다.
    """

    sale_id: str
    sale_item_id: str
    reservation_id: str
    status: Literal["RAN", "FAILED", "SHORT"]
    reason: str = ""
    shipped_qty_kg: Decimal = Decimal(0)
    #: 판매가 요구한 양 (`sale_items.quantity_kg`). 저장이 아니라 비교용 사본이다 —
    #: 완납 판정(`fully_shipped_sales`)이 이 값과 `shipped_qty_kg` 를 맞대 본다.
    required_qty_kg: Decimal = Decimal(0)

    # ── 관측 칸 (물류 문서 24 §5-㉣) ─────────────────────────────────────
    #
    # 판정에 안 쓴다. 되짚기용이다. 고아 예약처럼 사유 문장만으로 설명이 안 되는 실패를
    # 걷기에서 잡으려고 터진 순간을 값으로 남긴다 — 사유 문장만으로는 후보가 몇이었는지
    # 못 읽는다.
    #
    # 기본값이 전부 `None` 이다. 모르는 것을 0 으로 채우지 않는다 — 후보 0건과 후보를
    # 못 읽은 것은 다른 사실이다. 이 칸을 안 채우는 생성 지점도 그대로 선다.

    #: 판매 품목 (`sale_items.item_id`). `DueSaleItem` 에서 가져와 싣는 사본이다 —
    #: 걷기 요약의 FAILED 한 줄이 품목을 부르려고 둔다.
    item_id: str | None = None
    #: 물류가 실제로 확보한 양 (`ReservationResult.reserved_qty_kg`). 예약까지 못 갔거나
    #: 못 읽었으면 `None`.
    reserved_qty_kg: Decimal | None = None
    #: 터진 뒤 그날(`as_of`) FEFO 후보 수 · 가용합. 후보 읽기가 터지면 둘 다 `None`.
    candidate_lot_count: int | None = None
    candidate_available_kg: Decimal | None = None
    #: 터진 예외의 클래스 이름 (`type(exc).__name__`). 안 터졌으면 `None`.
    error_type: str | None = None
    #: 할당이 안 선 예약을 놓아줬는가 (`_release_stranded` 가 값으로 돌려준다).
    #: 놓아주기를 안 했으면 `None`. 사유 문장에서 뽑지 않는다.
    release_outcome: Literal["RELEASED", "RELEASE_FAILED"] | None = None


@dataclass(frozen=True)
class OutboundOut:
    """출고 단계 1회의 결과.

    ```text
    RAN           나갈 것이 있어서 한 건이라도 시도했다 — 품목별 결과는 `items`
    NOTHING_DUE   확인했고 나갈 것이 없다 — 정상이다
    FAILED        시도조차 못 했다 (연결 · 조회 실패) — 아무것도 바뀌지 않았다
    ```

    `NOTHING_DUE` 는 실패가 아니므로 `BLOCKED` 나 `FAILED` 로 바꾸지 않는다. 바꾸면 그날
    확정 판매가 없는 정상적인 날이 실패로 보인다.
    """

    as_of: date
    status: Literal["RAN", "NOTHING_DUE", "FAILED"]
    reason: str = ""
    items: tuple[SaleItemOutcome, ...] = ()
    #: `mark_sale_delivered` 가 실제로 `DELIVERED` 로 바꾼 판매.
    delivered_sales: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def failed_items(self) -> tuple[str, ...]:
        """터진 판매 품목. 나머지는 계속 돌았다.

        `SHORT` 는 여기 안 들어온다. 확보 0kg 은 터진 것이 아니라 사업 결과다.
        """
        return tuple(one.sale_item_id for one in self.items if one.status == "FAILED")

    @property
    def short_items(self) -> tuple[str, ...]:
        """확보가 0kg 이라 나간 것이 없는 판매 품목.

        결과에 보인다. 안 보이면 "그날은 아무 일도 없었다" 와 구별되지 않고,
        다음 날 왜 같은 판매가 또 잡히는지 읽는 사람이 모른다.
        """
        return tuple(one.sale_item_id for one in self.items if one.status == "SHORT")
