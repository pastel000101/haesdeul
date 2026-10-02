"""수금 실행 등록소 — 수금 파트 Protocol · 등록 · 조회.

수금을 실제로 부르는 것은 `service/collection.py` 의 `collect_receipts` 다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Literal, Protocol

from app.contracts.parts import CollectionPartOut

CollectionPart = Literal["finance"]

#: 수금을 실행하는 파트.
#:
#: 재무 하나다. 판매는 계약 결제조건과 채권 발생의 상업적 원천이지 "오늘 얼마
#: 입금됐다" 를 만들지 않는다. 물류·매입은 현금 흐름에 손대지 않는다.
#:
#: 하나짜리 등록소가 과한 것이 아니다. 이것이 있어야 "구현이 없다" 와 "오늘 들어올
#: 것이 없다" 를 가를 수 있다. 둘은 다른 사실이고, 뭉치면 재무 구현체가 빠진 날 조용히
#: 아무 일도 일어나지 않는다.
PARTS: tuple[CollectionPart, ...] = ("finance",)


class CollectionSource(Protocol):
    """그날 수금 사건을 반영하는 방법. 재무가 소유한다.

    마스터는 `as_of` 만 준다 — 어느 채권을 얼마 수금할지는 재무 사실이다.
    `InboundExecution` 과 같은 이유다: "오늘 무엇이 자격을 얻었나" 를 마스터는 모르고
    파트가 읽어야 안다.

    `due_date` 경과를 수금으로 읽지 않는다. 기일이 지난 것과 돈이 들어온 것은 다른
    사실이고, 접으면 없는 현금으로 매입 판단이 돈다.

    `conn` 은 받기만 한다. commit·rollback·close 를 하지 않는다 — 트랜잭션 경계는
    마스터가 쥔다.

    멱등: 같은 날 두 번 불러도 두 번 수금되면 안 된다. 재무 전이가 누적 target 으로
    적히는 것이 그 근거다 — 같은 target 을 두 번 넣으면 delta 가 0 이다
    (`app/finance/domain/collections.py` 의 "cumulative collection cannot regress").

    정본 축은 `(sim_run_id, financing_mode)` 이고, 이 Protocol 은 그 둘을 받지 않는다.
    "어느 실행의 장부인가" 는 실행 정체성이라 어댑터 생성 인자로 온다 —
    `LogisticsInboundExecution` · `LogisticsTransitionAdapter` 와 같은 자리이고, 등록
    조립(`registry/bootstrap.py`)에서 `SimRunBound` 로 호출 때 주입한다. `financing_mode`
    는 어댑터가 호출 안에서 재무에 묻는다. 호출마다 인자로 나르면 마스터가 매번 그 값을
    정하는 셈이 된다.

    :param as_of: 수금일. 달력일이다 (토·일·공휴일 포함).
    :returns: 무엇이 수금됐는지. 들어올 것이 없으면 `NOTHING_DUE` 이고 그것은 정상이다.
    """

    def collect(self, conn: Any, *, as_of: date) -> CollectionPartOut: ...


# ── 등록소 ──────────────────────────────────────────────────────────────
#
# 수금 사건을 반영하는 방법을 담는다. 전이 · 하루 넘김 · 취소 · 입고 등록소와 한
# 사전에 섞으면 "입고는 되는데 수금은 안 되는" 상태를 표현할 수 없다.

_COLLECTIONS: dict[CollectionPart, Any] = {}


def register_collection(part: CollectionPart, impl: Any) -> None:
    """수금 실행 구현을 등록한다. `registry/bootstrap.py` 의 `wire_registries` 가 부른다."""
    if part not in PARTS:
        raise ValueError(f"수금 실행 파트가 아니다: {part!r}. 가능: {', '.join(PARTS)}")
    _COLLECTIONS[part] = impl


def registered() -> Mapping[CollectionPart, Any]:
    """지금 등록된 수금 실행. 읽기용 사본이다."""
    return dict(_COLLECTIONS)


def missing() -> tuple[str, ...]:
    """수금 실행 구현이 등록되지 않은 파트. `PARTS` 순서를 지킨다."""
    return tuple(part for part in PARTS if part not in _COLLECTIONS)


def reset() -> None:
    """등록을 비운다. 검사용이다."""
    _COLLECTIONS.clear()
