"""승인 전이(승인 약정 → 입고 예정)의 묶음 · 실패 종류.

★ 2026-09-30 재구성 BL-015: `logistics/transition.py` 에서 옮겼다. 순서는 `service/transition.py`,
  입고 예정 계산은
  `domain/transition.py`, 등록소 표면은 `adapter.LogisticsTransitionAdapter`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.logistics.schemas.snapshot import InTransitItem


class LogisticsFixtureMissing(LookupError):
    """갱신할 runtime fixture 행이 없다.

    🔴 **없으면 새로 만들지 않는다.** 새 행에는 `evidence_grade` · `approved_by` ·
       나머지 두 status(`confirmed_inbound_status` · `confirmed_outbound_status`)를
       정해 넣어야 하는데 그것은 **물류가 근거를 갖고 내리는 판단**이다.
       없는 판단을 기본값으로 지어내면, 지어낸 값이 그날의 사실로 남는다.
    """


class InboundScheduleConflict(ValueError):
    """같은 `inbound_id` 가 **다른 사실**로 부딪혔다. 무결성 위반이다.

    ★ **어느 쪽이 진짜인지 여기서 고르지 않는다.** 기존을 남기면 이번 승인이 조용히
      사라지고, 새 것으로 갈아 끼우면 앞 승인이 조용히 사라진다. 둘 다 *"에러 없이
      틀리는"* 쪽이라 멈추는 것이 맞다.

    🔴 **바깥 트랜잭션이 통째로 롤백할 수 있어야 한다.** 이 예외는 DML 이 나가기 전에
       오르므로 마스터가 승인 전이 전체를 되돌릴 수 있다 (`apply_approval` 의
       `except` 가 `FAILED` 로 사유를 남긴다).
    """


class PurchaseReferenceMissing(LookupError):
    """매입 참조 매핑을 **받았는데** 이 회차의 값이 그 안에 없다.

    ★ 이 예외는 *"호출자가 매핑을 줬다"* 는 전제에서만 오른다. 매핑을 아예 안 받은
      호출(마스터 규약이 그렇다)은 여기 오지 않는다 — 그때는 `purchase_id` 가 `None` 인
      것이 **정상 상태**다. 두 경우를 가르는 것이 이 예외의 일이다.

      ```text
      purchase_ids=None    마스터 규약이 안 넘긴다      → purchase_id=None (정상)
      purchase_ids={…}     줬는데 이 회차가 빠졌다      → 멈춘다 (무결성)
      ```

    🔴 **대신할 값을 고르지 않는다.** 매핑에 값이 하나뿐이어도 그것을 쓰지 않는다 —
       `purchase_ids` 는 **회차별** 매핑이라, 다른 회차의 값을 집으면 이 물건이
       남의 매입 줄에 달린다. 도착 뒤 그 참조로 등급·단가를 읽으므로 그 오배정은
       **원가와 등급이 틀린 로트**로 굳는다.

    🔴 **`None` 을 넣고 넘어가지도 않는다.** 그 `None` 은 위 표의 첫 줄과 구별되지
       않아, *"규약이 안 넘긴다"* 와 *"줬는데 값이 빠졌다"* 가 같은 사실이 된다.
    """


# ── 마스터 전이 Protocol 어댑터 ─────────────────────────────────────────
#
# ★ **위의 두 함수를 감싸기만 한다.** 계산은 `build_next_inventory` 가, 쓰기는
#   `persist_inventory` 가 그대로 한다 — 여기 있는 것은 마스터가 부르는 호출 모양에
#   이름과 인자를 맞춰 주는 배선뿐이다.
#
# ⚠️ **걷어내기 쉽게 얹었다.** 물류가 자기 어댑터를 올리면 이 절만 통째로 지우면
#    되고, 위의 두 함수는 손댄 자리가 없다.
#
# 🔴 **물류가 예고한 모양과 다른 곳이 하나 있다 — 생성 인자 `sim_run_id` 다.**
#    물류 계약에는 *"생성 인자가 없습니다"* 로 적혀 있었다. 그런데
#    `persist_inventory` 의 WHERE 절이 `sim_run_id` 를 쓰고(위 `query`), 그 값은
#    **어느 실행의 장부인가**라는 실행 정체성이라 물류가 아니라 마스터가 정한다.
#    모듈 상수로 박으면 실행이 둘이 되는 날 물류 코드를 고쳐야 하므로, 배선 자리
#    (`app/main.py`)에서 눈에 보이게 주입받는다.


@dataclass(frozen=True)
class InventoryTransition:
    """승인 한 건이 만드는 재고 변화 **한 묶음**.

    🔴 **회차 낱개가 아니라 묶음인 이유가 둘이다.**

    ```text
    회차에는 target_state_date 가 없다   persist 가 어느 날 행에 쓸지 모른다
    arrival_schedule 이 비면 빈 목록이다  "쓸 것이 없다" 와 "어느 행인지 모른다" 가
                                          같아진다
    ```

    ★ **빈 승인도 그날 행을 `CONFIRMED_ZERO` 로 적어야 한다.** 회차를 낱개로 내면
      빈 약정에서 시퀀스 자체가 비어 `persist` 가 아무 일도 안 하게 되고, 그러면
      *"승인분이 없다"* 는 우리가 아는 사실이 장부에 안 남는다.
    """

    #: 이 변화가 설 날. **마스터가 준다** — 물류가 세지 않는다.
    target_state_date: date
    #: 이 변화의 출처. `persist_inventory` 가 fixture 행의 `source_ref` 에 그대로 적는다.
    source_ref: str
    #: `build_next_inventory` 가 낸 회차별 입고 예정. **비어 있을 수 있다.**
    items: tuple[InTransitItem, ...]
