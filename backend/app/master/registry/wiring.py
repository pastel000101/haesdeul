"""
wiring.py — 프로세스 전역 에이전트 레지스트리

마스터가 부를 수 있는 대상은 런타임에 등록된 것뿐이다. 각 파트의 어댑터를
`registry/bootstrap.py` 가 여기에 등록하고, 마스터는 등록된 이름만 호출한다.

어댑터가 등록되지 않은 것은 오류가 아니라 상태다. 그것을 예외로 다루면 API 가 500 을
낸다. 실제로는 "오늘 그 부서가 돌지 않는다" 와 같은 상황이므로 `E4_NOT_STARTED` 로
다룬다(정의서 §5.3).

    AgentRegistry.get()   미등록 → 예외      ← 마스터 등록 실수
    이 모듈의 사전 점검     미등록 → 목록 반환  ← 등록되지 않은 부서
"""

from __future__ import annotations

from app.contracts.envelope import AgentName
from app.master.domain.flow import ADVISORS
from app.master.registry.ports import AgentPort, AgentRegistry

_REGISTRY = AgentRegistry()

REQUIRED_FOR_PROCUREMENT: tuple[AgentName, ...] = (*ADVISORS, "purchase")

REQUIRED_FOR_SALES: tuple[AgentName, ...] = ("sales", "finance")
"""판매 사이클이 부르기 전에 있어야 하는 어댑터. 둘뿐이고, 둘인 이유가 있다.

```text
sales      제안자가 없으면 후보가 0이다 — 시작할 이유가 없다
finance    FINANCIAL_VALIDATION 이 최종 재검증 필수 capability 다
```

`inventory` 를 넣지 않는다. 판매 Flow 는 밴드 개념이 없어 물류가 못 답해도 시작하도록
설계했다(`sales_flow.py` 의 ② 단계 · 설계 §1-2). 필수 목록에 넣으면 그 결정을 등록 쪽에서
뒤집는 셈이다. 물류가 `PRE_SALES` 에 못 답하면 그것은 회신으로 오고 후보의 탈락 사유가
된다. 회신으로 오는 것과 등록이 빈 것은 다르다 — 뒤는 아무도 못 부르는 상태다.

`purchase` 도 넣지 않는다. 부족량이 있는 후보에만 필요한 조건부 대상이다 —
`app/contracts/envelope.py` 의 `CAPABILITY_ROUTING["ADDITIONAL_SUPPLY_CONTEXT"]` 가 매입
`SUPPLY_CAPACITY_QUERY` 로 보내고, 판매 Flow 는 후보가 그 capability 를 요구하고
부족량이 있을 때만 부른다. 조건부 대상을 문 앞 필수로 올리면 안 부르는 날에도 매입
미등록으로 판매가 선다.

`REQUIRED_FOR_PROCUREMENT` 와 겹치지만 같은 목록이 아니다. 매입은 조언자 둘 + 제안자이고
판매는 제안자 + 검증자다 — 한쪽을 다른 쪽으로 대신 쓰면 판매가 물류 미등록으로 서거나
매입이 물류 없이 돈다.
"""


def register(agent: AgentName, port: AgentPort) -> None:
    """어댑터를 등록한다. `registry/bootstrap.py` 의 `wire_registries` 가 부른다."""
    _REGISTRY.register(agent, port)


def registry() -> AgentRegistry:
    return _REGISTRY


def missing(required: tuple[AgentName, ...] = REQUIRED_FOR_PROCUREMENT) -> tuple[AgentName, ...]:
    """`required` 중 어댑터가 등록되지 않은 에이전트."""
    return tuple(a for a in required if not _REGISTRY.has(a))


def reset() -> None:
    """테스트 전용 — 등록을 비운다.

    주의: 이것만으로는 되돌릴 수 없다. 등록은 `app/main.py` 가 import 시점에 한 번
    한다(`registry/bootstrap.py` 의 `wire_registries`). 여기서 비우면 그 모듈은 이미
    import 돼 있어 다시 등록되지 않고, 그 프로세스에서 계속 빈 채로 남는다. 그러면
    테스트 실행 순서에 따라 결과가 갈린다 — 전체 스위트에서는 통과하고 부분 실행에서만
    깨지는 식이라 눈에 띄지 않는다.

    부르기 전에 `snapshot()` 을 뜨고 끝나면 `restore()` 한다. 루트 `tests/conftest.py` 가
    모든 테스트에 그것을 걸어 두므로, 이 함수를 그냥 불러도 그 테스트 밖으로는 안 샌다.
    """
    global _REGISTRY
    _REGISTRY = AgentRegistry()


def snapshot() -> dict[AgentName, AgentPort]:
    """지금 등록 상태. 되돌리기 위한 것이지 읽어서 판단할 값이 아니다."""
    return {name: _REGISTRY.get(name) for name in _REGISTRY.registered}


def restore(saved: dict[AgentName, AgentPort]) -> None:
    """`snapshot()` 뜬 상태로 되돌린다. 지금 등록된 것은 버린다.

    합치지 않는다. 테스트가 남긴 등록이 섞여 나가면 되돌린 것이 아니다.
    """
    reset()
    for name, port in saved.items():
        register(name, port)
