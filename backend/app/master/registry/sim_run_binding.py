"""등록소가 호출 때 실행 축을 받아 어댑터에 넘기는 얇은 자리.

`bootstrap.wire_registries` 는 앱이 뜰 때 한 번 돈다. 어댑터가 생성 때 축을 들면 그
값이 프로세스 시작 때 굳어, 하루 실행이 번인이 아닌 실행을 타는 날에도 바뀌지 않는다.

```text
매입 원장   승인 경로가 실행 행에서 축을 읽어 넘긴다   → 새 실행
물류 장부   생성 때 굳힌 축                            → 번인
```

그러면 두 장부가 서로 다른 실행에 앉는데 아무 오류도 나지 않는다. `domain/ledger.py` 의
`sim_run_id_for` 가 상수로 메우지 않는 것과 같은 이유다. 그래서 등록소는 호출 때 축을
받아 그 축의 어댑터를 만든다.

부서 어댑터의 서명은 그대로다. `LogisticsTransitionAdapter(sim_run_id=…)` 도
`FinanceCollectionAdapter(sim_run_id=…)` 도 생성자로 축을 받고, 달라지는 것은 언제
만드느냐뿐이다.

등록소 여섯(상태전이 · 하루 넘김 · 승인 취소 · 입고 · 수금 · 채권)이 같은 함수를 쓴다.
여섯 자리에 같은 `isinstance` 를 복사해 두면 하나만 고치는 날 "전이는 축을 받는데
입고는 안 받는" 상태가 되고, 그것은 이 모듈이 막으려는 갈림과 같은 모양이다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SimRunBound:
    """축을 호출 때 받아 어댑터를 만드는 등록 항목.

    ```python
    register_transition(
        "logistics",
        SimRunBound(lambda axis: LogisticsTransitionAdapter(sim_run_id=axis)),
    )
    ```

    등록 시점에 어댑터를 만들지 않는다. 만들면 그 순간 축이 굳고, 이 클래스가 있는
    이유가 없어진다.

    명시적인 표시다 — 「부를 수 있으면 공장」으로 보지 않는다. 어댑터 클래스도
    `partial` 도 전부 callable 이라, callable 여부로 가르면 축을 안 받는 구현을 실수로
    축과 함께 부르는 날이 온다. 감싼 것만 공장이다.

    :param factory: 축 하나를 받아 어댑터를 돌려주는 함수.
    """

    factory: Callable[[str], Any]


def bind_sim_run(impl: Any, sim_run_id: str | None) -> Any:
    """등록된 것을 이번 실행의 축으로 묶는다.

    ```text
    SimRunBound      축으로 어댑터를 만들어 돌려준다
    그 밖            그대로 돌려준다 — 축을 안 쓰는 구현이다
    ```

    축을 안 쓰는 구현은 건드리지 않는다. 예: `FinanceCancellationAdapter()` 는 생성자에
    축이 없다 — 그런 등록까지 감싸게 하면 아무 뜻 없는 껍데기가 늘어난다.

    빈 축으로는 묶지 않는다. `domain/ledger.py` 의 `sim_run_id_for` 와 같은 태도다 —
    축이 없는데 조용히 번인으로 떨어지면 그 갈림이 아무 오류도 내지 않는다. 여기서 막고
    사유를 낸다.

    :raises ValueError: 감싼 등록인데 축이 비었을 때.
    """
    if not isinstance(impl, SimRunBound):
        return impl
    if not sim_run_id or not sim_run_id.strip():
        raise ValueError(
            "sim_run_id 없이 등록소 어댑터를 묶을 수 없다 — 어느 실행의 장부인지가"
            " 없으면 매입 원장과 다른 실행에 앉는다. 상수로 메우지 않는다"
        )
    return impl.factory(sim_run_id)
