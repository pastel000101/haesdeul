"""SUPPLY_CAPACITY_QUERY — 판매 부족분에 낼 경계를 위해 시세를 읽고 가능량을 계산한다 (E4-7).

```text
read_supply_capacity   포트로 그날 시세를 한 번 읽는다 → domain `compute_supply_capacity`
```

8노드 그래프를 안 돈다 — 답은 안이 아니라 경계다 (`domain/supply_capacity.py` 머리말).
어느 품목을 묻는지 가리는 것과 회신 본문 · 근거 · 설명문을 만드는 것은 어댑터
(`adapter._supply_capacity_query`)다. 설정은 어댑터가 읽어 넘기고, 여기서는 시세 → 계산
차례로 돈다.

DB 에 쓰지 않는다. 시세 조회 연결은 시세 공급자(`readmodel/quotes.py`)가 빌린다.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.purchase_agent import ports
from app.purchase_agent.domain.supply_capacity import SupplyCapacity, compute_supply_capacity
from app.purchase_agent.readmodel.quotes import QuoteSource


@dataclass(frozen=True)
class SupplyCapacityReading:
    """읽은 시세와 그 시세로 잡은 경계.

    시세를 같이 돌려준다 — 같은 시세를 회신의 관측일(`adapter._observed_at`)도 봐야
      한다. 두 번 읽으면 그 사이에 적재가 들어와 경계와 관측일이 다른 조회에서 나온다.
    """

    market_quotes: list[dict[str, Any]]
    capacity: SupplyCapacity


def read_supply_capacity(
    item: str,
    as_of: date,
    constraints: Mapping[str, Any],
    *,
    quotes: QuoteSource | None,
    warehouse_free_kg: float | None,
    finance_cap_amount_krw: float | None,
) -> SupplyCapacityReading:
    """그날 시세를 읽고 창고 · 재무 값과 함께 가능량 경계를 잡는다.

    ``quotes`` 는 등급별 시세 공급자다 — ``None`` 이면 mock 이고 운영 등록은 실 경락가를
    꽂는다 (`adapter.purchase_port` docstring). ``constraints`` 는 부르는 쪽이 읽은 선언을
    그대로 받는다 — 회신의 관측일도 같은 선언으로 판정한다.
    """
    market_quotes = ports.get_market_quotes(item, as_of, source=quotes)
    capacity = compute_supply_capacity(
        quotes=market_quotes,
        warehouse_free_kg=warehouse_free_kg,
        finance_cap_amount_krw=finance_cap_amount_krw,
        constraints=constraints,
    )
    return SupplyCapacityReading(market_quotes=market_quotes, capacity=capacity)
