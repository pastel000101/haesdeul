"""물류 상태 어휘 — 여러 모듈이 같은 집합으로 세야 하는 것을 **한 벌로** 둔다.

★ 2026-09-30 재구성 BL-015: 손으로 복제해 «같아야 한다» 는 주석으로 묶여 있던 상수를 모았다.

  ```text
  USAGE_SCOPE                  transition.USAGE_SCOPE · repository.LOGISTICS_POLICY_USAGE_SCOPE
  HOLDING_RESERVATION 등 넷     outbound (disposal 의 튜플 두 벌)
  OCCUPYING_PALLET             warehouse (auto_maintenance 의 튜플)
  RECEIPT_BEFORE_INSPECTION    inspections._BEFORE_INSPECTION · inbound_execution._NEEDS_INSPECTION
  RECEIPT_INSPECTION_SETTLED   inspections._INSPECTION_DONE · inbound_execution._INSPECTION_SETTLED
  ACTIVE_LOT_STATUS            tools · monitoring.detect · monitoring.observe · query.tools
  DISPOSED_LOT_STATUS          monitoring.observe
  ```

  값은 그대로다. SQL 인자로 넘길 때는 `sorted()` 로 순서를 정한다(`= ANY` 라 결과는 같다 —
  폐기 · 자동 유지보수는 종전에 튜플을 `list()` 로 넘겼다).
"""

from __future__ import annotations

#: 조회·갱신 대상 범위. `repository.py` 의 `LOGISTICS_POLICY_USAGE_SCOPE` 와 같은 값이다.
USAGE_SCOPE = "AGENT_MVP_DEMO"


#: 🔴 **아직 재고를 잡고 있는** 예약 상태. `RELEASED` · `CANCELLED` 는 놓아준 것이다.
HOLDING_RESERVATION: frozenset[str] = frozenset({"RESERVED", "PARTIALLY_ALLOCATED", "ALLOCATED"})

#: 🔴 **이미 놓아준** 예약 상태. `released_as_of` 가 적혀 있어야 하는 것이 이것이다
#: (WP-3 M3). 위 집합의 여집합이지만 **손으로 적는다** — 어휘가 늘 때 어느 쪽에
#: 들어가는지 자동으로 정해지면 안 되는 자리다.
RELEASED_RESERVATION: frozenset[str] = frozenset({"RELEASED", "CANCELLED"})

#: 🔴 **아직 창고에서 안 나간** 할당 상태. 가용량에서 빼야 하는 것이 이것이다.
#:
#: ★ `SHIPPED` 는 빼지 않는다 — 그 몫은 이미 원장 OUT 이 `remaining_qty_kg` 에서
#:   덜어냈으므로, 여기서 또 빼면 **같은 수량을 두 번 차감**하게 된다.
HOLDING_ALLOCATION: frozenset[str] = frozenset({"ALLOCATED", "PICKED"})

#: 🔴 예약이 **이미 Lot 에 배정한** 몫. 예약의 *"아직 안 배정된 잔여"* 를 셀 때 뺀다.
#:
#: ★ `SHIPPED` 도 포함한다 — 나간 몫은 그 예약이 더 이상 새로 잡아 둘 필요가 없다.
#:   빼지 않으면 출고 뒤에도 예약이 원래 총량을 계속 잡고 있는 것으로 보여
#:   **같은 수량이 잔량 감소와 예약 양쪽에서 두 번 깎인다.**
ASSIGNED_ALLOCATION: frozenset[str] = frozenset({"ALLOCATED", "PICKED", "SHIPPED"})


#: 🔴 **자리를 차지하는** Pallet 상태. `ck_pallets_location_matches_status` 가
#: 이 둘에만 `current_location_id` 를 허용한다 — 정원 계산과 같은 집합이어야 한다.
OCCUPYING_PALLET: frozenset[str] = frozenset({"ACTIVE", "HOLD"})


#: 검수 사실을 아직 안 가진 Receipt 상태. 여기서만 새 검수를 만들 수 있다.
RECEIPT_BEFORE_INSPECTION: frozenset[str] = frozenset({"ARRIVED", "INSPECTING"})

#: 검수 단계가 끝난 Receipt 상태. **되돌리지 않는다.**
RECEIPT_INSPECTION_SETTLED: frozenset[str] = frozenset({"INSPECTED", "PUTAWAY_DONE", "CLOSED"})


#: 가용재고로 인정하는 Lot 상태. DB에 없는 상태를 새로 만들지 않는다 —
#: ACTIVE가 아닌 상태(검수/격리/사용불가 등)는 물리 점유만 하고 가용에서 빠진다.
ACTIVE_LOT_STATUS = "ACTIVE"


DISPOSED_LOT_STATUS = "DISPOSED"
