"""입고 실행 등록소 — 입고 파트 Protocol · 등록 · 조회.

입고를 실제로 부르는 것은 `service/inbound.py` 의 `receive_arrivals` 다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Literal, Protocol

from app.contracts.parts import InboundPartOut

InboundPart = Literal["logistics"]

#: 입고를 실행하는 파트.
#:
#: 물류 하나다. 재무·매입은 도착 자체를 실행하지 않는다 — 재무는 지급일에 움직이고
#: 매입은 승인에서 끝난다.
#:
#: 하나짜리 등록소가 과한 것이 아니다. 이것이 있어야 "구현이 없다" 와 "오늘 받을 것이
#: 없다" 를 가를 수 있다. 둘은 다른 사실이고, 뭉치면 물류 어댑터가 빠진 날 조용히 아무
#: 일도 일어나지 않는다.
PARTS: tuple[InboundPart, ...] = ("logistics",)


class InboundExecution(Protocol):
    """도착분을 실제로 받는 방식. 물류가 소유한다.

    `build` 를 순수하게 나누지 않는다. "오늘 무엇이 도착 예정인가" 를 마스터는 모르고
    물류가 읽어야 안다 — `DayOpening` · `ApprovalCancellation` 과 같은 이유다.

    `conn` 은 받기만 한다. commit·rollback·close 를 하지 않는다 — 트랜잭션 경계는
    마스터가 쥔다.

    멱등: 같은 날 두 번 불러도 두 번 입고되면 안 된다. 판정 기준은 물류가 정한다 —
    `inbound_id` 로 볼지, 로트 존재로 볼지는 물류 지식이다.

    당일 도착만 처리하는 것이 아니다(물류 회신 §5).

      ```text
      expected_arrival_date >  as_of   not_due
      expected_arrival_date == as_of   due
      expected_arrival_date <  as_of   overdue 이지만 여전히 due   ← 빠뜨리면 안 된다
      ```

      어느 날 `receive_arrivals` 가 돌지 않았어도 다음 날이 밀린 것을 받는다. 당일만
      보면 그 하루에 도착 예정이던 물건이 `in_transit` 에 계속 남는다(예: 도착 예정
      2026-01-07 분이 02-06 까지 `in_transit` 에 남았던 관측).

      판정은 파트가 한다. 마스터는 `as_of` 만 준다 — "무엇이 도착 자격을 얻었나" 는
      물류 지식이다.

    멱등 축은 `(sim_run_id, inbound_id)` 다(물류 회신 §6).

      ```text
      도메인 식별          inbound_id
      실행/DB 중복 방지    (sim_run_id, inbound_id)
      ```

      같은 `inbound_id` 문자열이라도 다른 `sim_run_id` 는 별개의 실행 장부다.

      `sim_run_id` 는 이 Protocol 이 받지 않는다. "어느 실행의 장부인가" 는 실행
      정체성이라 어댑터 생성 인자로 온다 — `LogisticsTransitionAdapter` ·
      `LogisticsCancellationAdapter` 와 같은 자리이고, 등록 조립(`registry/bootstrap.py`)
      에서 `SimRunBound` 로 호출 때 주입한다. 호출마다 나르면 마스터가 매번 그 값을 정하는
      셈이 된다.

    :param as_of: 받는 날. 달력일이다 (토·일·공휴일 포함).
    :returns: 무엇을 받았는지. 받을 것이 없으면 `NOTHING_DUE` 이고 그것은 정상이다.
    """

    def receive(self, conn: Any, *, as_of: date) -> InboundPartOut: ...


# ── 등록소 ──────────────────────────────────────────────────────────────
#
# 도착분을 받는 방법을 담는다. 전이 · 하루 넘김 · 취소 등록소와 한 사전에 섞으면
# "전이는 되는데 입고는 안 되는" 상태를 표현할 수 없다.

_INBOUNDS: dict[InboundPart, Any] = {}


def register_inbound(part: InboundPart, impl: Any) -> None:
    """입고 실행 구현을 등록한다. `registry/bootstrap.py` 의 `wire_registries` 가 부른다."""
    if part not in PARTS:
        raise ValueError(f"입고 실행 파트가 아니다: {part!r}. 가능: {', '.join(PARTS)}")
    _INBOUNDS[part] = impl


def registered() -> Mapping[InboundPart, Any]:
    """지금 등록된 입고 실행. 읽기용 사본이다."""
    return dict(_INBOUNDS)


def missing() -> tuple[str, ...]:
    """입고 실행 구현이 등록되지 않은 파트. `PARTS` 순서를 지킨다."""
    return tuple(part for part in PARTS if part not in _INBOUNDS)


def reset() -> None:
    """등록을 비운다. 검사용이다."""
    _INBOUNDS.clear()
