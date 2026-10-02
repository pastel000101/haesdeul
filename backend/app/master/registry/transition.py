"""승인 전이 등록소 — 재무 · 물류 전이 Protocol · 등록 · 조회.

전이를 실제로 부르는 것은 `service/transition.py` 의 `apply_approval` 이다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, Literal, Protocol

from app.contracts.commitment import ApprovedCommitment

TransitionPart = Literal["finance", "logistics"]

#: 전이에 참여하는 파트와 호출 순서. 재무가 먼저다 — 현금이 모자라 재무가 터지면
#: 재고 쪽은 손도 대지 않은 채 롤백된다.
PARTS: tuple[TransitionPart, ...] = ("finance", "logistics")


# ── 두 파트가 같은 모양을 갖는다 ────────────────────────────────────────
#
# 재무와 물류의 Protocol 은 같은 인자를 받는다. 두 모양으로 다를 근거가 없고, 다르게
# 적으면 규약이 실제 구현과도 어긋난다.
#
# 공통은 `commitment` 와 `target_state_date` 다. 두 파트가 같은 승인분을 같은 날짜
# 기준으로 옮긴다. 날짜를 계산 자리(`build`)에서 받는 이유: 계산이 날짜를 못 받으면 그
# 값이 write 자리(`persist`)로 밀려나고, 순수 계산과 write 의 경계가 날짜 때문에 흐려진다.
#
# `purchase_ids` 도 공통이다(물류 요청 `#311`).
#
#   ```text
#   재무   payables.purchase_id 가 purchases 를 참조하는 NOT NULL 컬럼이다
#   물류   도착 시점에 purchase_items 의 권위값을 읽어야 한다
#          (purchase_item_id · item_id · grade · unit_price_krw_per_kg)
#   ```
#
#   둘 다 매입 원장을 가리켜야 하고, 둘 다 그 ID 를 지어낼 수 없다. 만들 자리는 승인을
#   쥔 마스터다(`domain/purchase_ids.py` 의 `purchase_id_for`). 어느 부서에 그 값이
#   필요한지는 마스터가 단정하지 않는다 — 판정할 자리는 그 값을 쓰는 부서다. 물류에
#   주지 않으면 `InTransitItem.purchase_id` 가 None 이 되어 도착일에 Arrival 이 막힌다.


class FinanceTransition(Protocol):
    """승인분이 현금 장부를 바꾸는 방식. 재무가 소유한다.

    `build` 는 순수 계산이고 `persist` 는 write 다. 나누는 이유는 하나다 — 계산이
    실패하면 DB 를 열지도 않은 채 멈출 수 있어야 한다.

    `persist` 는 인자가 `(conn, …)` 두 개뿐이고 commit 하지 않는다. 커밋은 두 파트가
    모두 끝난 뒤 `apply_approval` 이 한 번 한다.

    :param target_state_date: 승인 결과 상태가 설 날. 마스터가 준다 — 재무가 실행일
        달력을 소유하지 않는다.
    :param purchase_ids: 회차(seq) → purchase_id 매핑이다. 단수가 아닌 이유는
        `purchases.purchase_date` 가 header 에 하나뿐이기 때문이다. 회차마다 매입일이
        다르므로 한 header 에 여러 회차를 담을 수 없고, 따라서 회차마다 `purchases`
        한 행이 선다.
    """

    def build(
        self,
        commitment: ApprovedCommitment,
        *,
        target_state_date: date,
        purchase_ids: Mapping[int, str],
    ) -> object: ...
    def persist(self, conn: Any, row: object) -> None: ...


class LogisticsTransition(Protocol):
    """승인분이 재고 장부를 바꾸는 방식. 물류가 소유한다.

    재무와 달리 여러 행이 나온다 — 회차별 입고가 각각 로트/이동이 된다. 몇 행인지도
    물류가 정한다.

    `persist` 는 재무와 같이 인자가 `(conn, …)` 두 개뿐이고 commit 하지 않는다.

    `purchase_ids` 도 재무와 같은 값·같은 모양으로 받는다(물류 요청 `#311`). 물류 WMS 가
    도착 시점에 `purchase_items` 의 권위값(`purchase_item_id` · `item_id` · `grade` ·
    `unit_price_krw_per_kg`)을 읽어야 하고, 그러려면 매입 원장을 가리키는 ID 가 필요하다.
    그것을 만드는 곳은 마스터(`purchase_id_for`)이고 물류는 생성·파싱·재조합을 하지
    않는다.
    """

    def build(
        self,
        commitment: ApprovedCommitment,
        *,
        target_state_date: date,
        purchase_ids: Mapping[int, str],
    ) -> Sequence[object]: ...
    def persist(self, conn: Any, rows: Sequence[object]) -> None: ...


# ── 등록소 ──────────────────────────────────────────────────────────────
#
# `wiring.py` 의 에이전트 레지스트리와 같은 결이다. 다른 점은 하나 — 저쪽은
# 부를 대상을 담고 여기는 장부를 바꿀 방법을 담는다. 한 사전에 섞으면
# 어댑터가 없는 것과 전이가 없는 것이 같은 문장으로 나가고, 둘은 다른 사실이다.

_TRANSITIONS: dict[TransitionPart, Any] = {}


def register_transition(part: TransitionPart, impl: Any) -> None:
    """전이 구현을 등록한다. `registry/bootstrap.py` 의 `wire_registries` 가 부른다."""
    if part not in PARTS:
        raise ValueError(f"전이 파트가 아니다: {part!r}. 가능: {', '.join(PARTS)}")
    _TRANSITIONS[part] = impl


def registered() -> Mapping[TransitionPart, Any]:
    """지금 등록된 전이. 읽기용 사본이다 — 밖에서 넣지 못하게 한다."""
    return dict(_TRANSITIONS)


def missing() -> tuple[str, ...]:
    """전이 구현이 등록되지 않은 파트. `PARTS` 순서를 지킨다.

    순서를 지키는 이유는 사유 문장 때문이다. 집합 순서로 적으면 같은 상황이 실행마다
    다른 문장으로 나가 로그를 비교할 수 없다.
    """
    return tuple(part for part in PARTS if part not in _TRANSITIONS)


def reset() -> None:
    """테스트 전용 — 등록을 비운다."""
    _TRANSITIONS.clear()
