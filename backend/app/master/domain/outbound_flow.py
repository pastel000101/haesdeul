"""출고 결과 판정 — 그날 나갈 줄 거르기, 판매 단위 완납 판정, 확보량 읽기."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from app.master.schemas.outbound_flow import DueSaleItem, SaleItemOutcome


def due_today(rows: Sequence[DueSaleItem], as_of: date) -> tuple[DueSaleItem, ...]:
    """오늘 나갈 것만 남긴다. `sales.sale_date` 가 정본이다.

    다른 날 것이 섞이면 아직 안 팔 물건이 오늘 창고를 나간다. 그러면 그날 장부는 맞는데
    그 앞뒤 날의 재고가 전부 틀린다 — 에러는 안 난다.
    """
    # `<=` 다 — 휴장일 납품이 그 뒤 첫 개장일에 오는 것은 조회가 이미 골랐다.
    return tuple(row for row in rows if row.sale_date <= as_of)


def fully_shipped_sales(results: Sequence[SaleItemOutcome]) -> tuple[str, ...]:
    """모든 품목이 요구량만큼 나간 판매만. 일부만 나갔으면 여기 안 들어온다.

    한 판매에 품목이 셋인데 둘만 나간 날 `DELIVERED` 로 적으면, 그 판매는 영원히
      나머지 하나를 못 받는다 — 다음 날 `order_status` 필터가 그 판매를 아예 안
      집기 때문이다.

    `status == "RAN"` 만으로는 완납이 아니다 (물류 PR #484 수신요청 §5.2).

      ```text
      RAN     출고 단계를 탔다
      완납    shipped_qty_kg >= required_qty_kg
      ```

      100kg 주문에 60kg 이 나가도 단계는 끝까지 돈다. 그것을 `DELIVERED` 로 닫으면
      나머지 40kg 이 영원히 안 나간다. 상태는 `RAN` 그대로 둔다 — 단계를 탄 것은
      사실이고, 부족한 것은 완납이 아니라는 사실뿐이다.

    부분 출고된 판매를 다음 날 다시 잡지 않는다 — 재출고(backorder)는 MVP 밖이다
       (07 확정 구현결정서 §6 «부분출고: 재출고 없음» · §15 DEFER). `due_sale_items` 가
       그날 판매(휴장이면 그 뒤 첫 개장일에 한 번)만 읽는 것이 그 정책이고, 미충족 몫은
       예약의 미할당량으로 보이게 남는다. 그 어휘(부분 납품 `order_status`)는 판매 소유다.
       주의: `sale_date <= as_of` 를 «다음 날 다시 잡혀 나머지를 시도한다» 는 전제로
       고치면 WP-3 의 «예약이 선 날 = sale_date» 복원이 깨진다.

    순서를 지킨다. 먼저 나온 판매가 먼저다 — 같은 날을 두 번 돌려도 목록이 같다.
    """
    order: list[str] = []
    ok: dict[str, bool] = {}
    for one in results:
        if one.sale_id not in ok:
            order.append(one.sale_id)
            ok[one.sale_id] = True
        ok[one.sale_id] = ok[one.sale_id] and _is_complete(one)
    return tuple(sale_id for sale_id in order if ok[sale_id])


def _is_complete(one: SaleItemOutcome) -> bool:
    """이 품목이 요구량만큼 나갔는가."""
    return one.status == "RAN" and one.shipped_qty_kg >= one.required_qty_kg


def reserved_qty_of(reserved: Any) -> Decimal | None:
    """`ReservationResult.reserved_qty_kg` — 물류가 실제로 확보한 양.

    칸이 없으면 `None` 이다. 0 이 아니다. "확보가 0이었다" 와 "얼마나 확보됐는지 못
    읽었다" 는 다른 사실이라, 못 읽은 것을 0으로 접으면 물류가 칸 이름을 바꾼 날 모든
    출고가 조용히 shortage 가 된다. 못 읽었으면 예약이 선 것으로 보고 할당까지 가고, 예약이
    없으면 물류가 터뜨려 `FAILED` 로 보이게 남는다.
    """
    raw = getattr(reserved, "reserved_qty_kg", None)
    if raw is None:
        return None
    try:
        return Decimal(raw)
    except (TypeError, ValueError, ArithmeticError):
        return None
