"""그날 승인에 적힌 **실매입 합계** — 화면 둘(대시보드 · 매입 탭)이 읽는다.

```text
repository   purchase_record_repository.recorded_sums_by_plan   (품목, 안 이름)마다 수량 · 금액 합
readmodel    recorded_totals_by_plan (여기)                     → {(품목, 안 이름): RecordedTotals}
화면         api/dashboard/query._records · api/purchase/query._records
             못 읽으면 빈 표로 두고 화면을 띄운다 (그 태도는 화면이 정한다)
```

🟢 2026-09-29 (재구성 BL-012) 전에는 `RecordedTotals` 와 이 조립이 SQL 과 한 함수
   (`master/purchase_record_repository.recorded_totals_by_plan`)에 있었고, 화면이 그
   repository 를 직접 불렀다. SQL 은 repository 에 두고 결과 모델과 단가 계산을 여기로
   옮겼다. 함수 이름 · 인자 · 돌려주는 값은 그대로다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.master.purchase_record_repository import recorded_sums_by_plan

__all__ = ["RecordedTotals", "recorded_totals_by_plan"]


@dataclass(frozen=True)
class RecordedTotals:
    """한 승인에 적힌 실매입의 **합계**. 회차가 여럿이면 그 합이다.

    ★ 안의 제안값과 **다른 사실**이다. 「사자고 낸 값」이 아니라 「실제로 산 값」이다.

    🔴 `unit_price` 의 `None` 은 «단가가 없다» 가 아니라 **«정수 단가로 안 떨어진다»** 다.
       금액은 사람이 실제로 낸 돈이라 그것이 정본이고, 나누어떨어지지 않는다고 반올림해
       보이면 화면의 `단가 × 수량` 이 금액 칸과 어긋난다 — `purchase_record._grade_lines`
       가 같은 이유로 금액 대신 줄을 하나 더 얹는다.
    """

    qty_kg: float
    amount_krw: int
    unit_price: int | None


def _totals(quantity_kg: Any, amount_krw: Any) -> RecordedTotals:
    """합계 두 개에서 단가까지. **정수 나눗셈으로만** 센다 — 부동소수로 어림하지 않는다."""
    qty = float(quantity_kg)
    amount = round(float(amount_krw))
    unit: int | None = None
    if qty.is_integer() and int(qty) > 0 and amount % int(qty) == 0:
        unit = amount // int(qty)
    return RecordedTotals(qty_kg=qty, amount_krw=amount, unit_price=unit)


def recorded_totals_by_plan(
    *, sim_run_id: str, as_of: date
) -> dict[tuple[str, str], RecordedTotals]:
    """그 실행 축 · 그날 승인에 적힌 실매입 합계. 열쇠는 `(품목, 안 이름)`.

    열쇠를 왜 그 둘로 잡는지, 축과 기준일을 왜 둘 다 거는지는 SQL 자리
    (`purchase_record_repository.recorded_sums_by_plan`)에 적혀 있다.

    ⚠️ 적힌 기록이 없으면 **빈 표**다. 0 으로 채우지 않는다 — «안 샀다» 와 «못 읽었다» 는
      부르는 쪽이 가린다. 읽다가 난 예외도 그대로 올려 보낸다.
    """
    return {
        (str(row["item"]), str(row["scenario_label"])): _totals(
            row["quantity_kg"], row["amount_krw"]
        )
        for row in recorded_sums_by_plan(sim_run_id=sim_run_id, as_of=as_of)
    }
