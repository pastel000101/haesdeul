"""판매 승인(확정 + 예약) 결과 모델.

★ 2026-09-30 재구성 BL-018: `master/sales_approval.py` 에서 옮겼다 — `SaleConfirmationOut`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class SaleConfirmationOut(BaseModel):
    """판매 승인 1건이 원장에 남긴 결과.

    🔴 **세 값을 섞지 않는다** (`TransitionOut` 과 같은 규율).

      ```text
      CONFIRMED   sales · sale_items 가 섰다
      BLOCKED     확정할 수 없었다 — 아무것도 안 썼다 (재검증 미통과 · 없는 상업조건)
      FAILED      쓰려다 실패했다 — 롤백했다
      ```

      `BLOCKED` 를 `FAILED` 로 접으면 *"값이 없어 못 한 것"* 이 장애로 읽히고,
      반대로 접으면 **실패한 확정이 "조건이 없었다" 로 조용히 묻힌다.**
    """

    status: Literal["CONFIRMED", "BLOCKED", "FAILED"]
    reason: str = ""

    #: 🔴 **비어 있지 않으면 그것이 막은 이유다.** 부서 판정과 다른 칸에 둔다 —
    #: `validations` 에 가짜 항목을 밀어 넣으면 화면이 부서를 보러 간다.
    #:
    #: ★ **담는 것은 원인 어휘다** (`missing_term_origins`) — 칸 이름이 아니다.
    #:   `REQUEST_MISSING_<FIELD>` · `TERMS_UNRESOLVED_<FIELD>` 이고, 칸 이름은
    #:   `term_of_origin` 으로 되꺼낸다. 이름만 담으면 화면이 *"누가 고쳐야 하나"*
    #:   를 다시 추측하게 된다.
    missing_terms: list[str] = Field(default_factory=list)

    sale_id: str | None = None
    sale_item_id: str | None = None

    #: 🔴 **출고는 여기서 안 한다.** 확정과 출고가 같은 클릭에 붙으면 *"승인했지만
    #: 아직 안 나갔다"* 라는 상태가 사라진다 — 그 뒤는 기존 orchestration 이다.
    shipped: bool = False

    # ── 확정분 예약 (2026-09-12) ────────────────────────────────────────
    #
    # 🔴 **확정만 서고 예약이 없으면 같은 재고를 두 번 판다** (`SIM-CHAIN-V4` 실측).
    #
    #   ```text
    #   확정 (D일)   sales 행이 CONFIRMED 로 선다 · 예약이 없었다
    #   출고 (D+1)   ship_due_sales 가 그때서야 reserve 를 불렀다
    #   그 사이      available_qty_kg = remaining_qty_kg − held_qty_kg 인데 held 가
    #                안 올라 **같은 재고가 다음 날 또 팔렸다**
    #   ```
    #
    #   물류가 이 위험을 이미 적어 뒀다 (`app/logistics/service/outbound.py` 의
    #   `item_free_stock_qty`): *"아직 Lot 을 안 고른 예약은 … Lot 가용량에서 안
    #   빠진다 — 그것만 보면 같은 재고를 두 번 예약하게 된다."*

    #: 물류가 이 확정분을 잡아 둔 예약 이름. 🔴 **여기서 짓지 않는다** —
    #: `reservation_id_for_sale_item` 이 `sale_item_id` 에서 계산한 값 그대로다.
    #: 출고가 같은 함수로 같은 값을 계산하므로 둘이 **같은 한 예약**을 가리킨다.
    reservation_id: str | None = None

    #: 판매가 정한 요구량. 🔴 **마스터도 물류도 안 고친다.**
    required_qty_kg: Decimal | None = None

    #: 물류가 **실제로 잡은 양.** 모자란 날 이 값만 작아진다.
    reserved_qty_kg: Decimal | None = None

    #: 확정분 예약 어휘. 🔴 **`status` 와 축이 다르다.**
    #:
    #: ```text
    #: RESERVED  요구량만큼 잡았다
    #: SHORT     모자랐다 — 🔴 그래도 확정은 CONFIRMED 다
    #: None      예약이라는 사건 자체가 없었다 (확정이 안 섰다)
    #: ```
    #:
    #: ★★ **모자랐다는 사실이 값으로 남아야 한다.** `BLOCKED` 로 되돌리면 이미 선
    #:   확정을 거짓으로 만들고, 아무 데도 안 남기면 오늘 밤과 같은 일이 반복된다 —
    #:   걷기 요약이 이 어휘를 센다 (`backtest_runner.format_summary` 의 「예약어휘」).
    reservation_outcome: Literal["RESERVED", "SHORT"] | None = None
