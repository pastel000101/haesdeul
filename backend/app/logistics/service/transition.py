"""승인 약정을 물류 재고 상태로 옮기는 build·persist (C 형태 ⑦).

마스터가 `app/master/service/transition.py` 에서 트랜잭션 경계를 쥐고, 무슨 값을 어느 칸에
어떤 SQL 로 쓸지는 물류가 소유한다. 이 파일이 그 물류 몫이다.

```text
승인 → ApprovedCommitment → build_next_inventory  (순수 계산 · DB 를 안 부른다)
                          → persist_inventory     (주어진 conn 으로 write · commit 안 함)
```

왜 `inventory_lots` 가 아니라 runtime fixture 인가.
승인 시점의 입고 예정을 `inventory_lots` 에 넣을 수 없다 (2026-09-03 실측).
네 가지가 막는다.

   ```text
   status CHECK       ACTIVE · DEPLETED · DISPOSED · HOLD   IN_TRANSIT 이 없다
   move_type CHECK    IN · OUT · DISPOSE · ADJUST           입고 예정 값이 없다
   purchase_item_id   NOT NULL + FK → purchase_items        승인만으론 그 행이 없다
   unit_cost · zone   NOT NULL                              승인 시점에 없거나 추정이다
   ```

   네 가지 중 어느 하나도 코드로 우회할 수 없다. 상태값을 지어내면 CHECK 가
   막고, 막지 않게 스키마를 열면 "아직 안 온 물건" 이 실재 로트와 같은 칸에 앉는다.
   `unit_cost` 를 추정으로 채우면 그 추정이 원가가 되어 재무로 흘러간다.

입고 예정의 정본은 `inbound_schedules` 다 (W3-3).
승인이 그 표에 한 행을 적고, Reader 가 날짜로 질의한다 — 날짜별 fixture JSON 에
복제하지 않는다. 계약은 `schemas/snapshot.py` 의 `InTransitItem` 그대로이고,
`in_transit_status` 는 Header 표시(그 축을 확인했나)로만 남는다.

매입 참조(`purchase_id`)는 마스터가 넘긴다. 운송 중인 물건이 도착하면 물류는 그 매입
줄에서 `purchase_item_id` · `item_id` · `grade` · `unit_price_krw_per_kg` 를 읽는다. 그
참조를 물류가 지어내면 안 되고, 만드는 곳은 마스터다
(`app/master/domain/purchase_ids.py` 의 `purchase_id_for`).

   ```text
   마스터 경로   logistics.build(…, purchase_ids={leg.seq: purchase_id})
                 → purchase_ids[leg.seq] 를 그대로 보관
   매핑 없는 호출 logistics.build(commitment, target_state_date=…)
                 → purchase_ids=None → purchase_id=None
   ```

   마스터 전이 규약(`app/master/registry/transition.py` 의 `LogisticsTransition.build`)은
   `purchase_ids` 를 필수로 받고, 마스터 `service/transition.py` 의 `apply_approval` 이
   그 매핑을 넘긴다. 물류 쪽 기본값 `None` 은 매핑 없이 부르는 호출을 위한 것이다.

   참조가 없는 행은 도착일에 `blocked` 로 드러난다. `arrival.select_due_inbound`
   가 `purchase_id` 없는 행을 `due` 로 넘기지 않는다. 조용히 통과시키지 않으므로
   "참조 없이 만들어진 행" 이 로트가 되는 일은 없다.

`in_transit` → 실제 입고(`inventory_lots`) 전환은 다음 모듈들이 한다.

   ```text
   arrival.select_due_inbound       도착 자격 판정 (eta · inbound_id · purchase_id)
   purchase_detail.fetch_...        매입 줄 조회 — 등급·단가의 권위 출처
   receipts.create_arrived_receipt  ARRIVED Receipt
   inspections.record_inspection    검수 → INSPECTED
   inbound_stock.materialize_...    accepted 수량으로 Lot 생성 + 원장 IN → PUTAWAY_DONE
   ```

   이 파일은 그 경로에 관여하지 않는다. 여기가 하는 일은 승인 시점의 입고 예정을
   적는 것까지이고, 그 뒤는 위 모듈들이 각자 소유한다.

`confirmed_inbound_status` 도 승인에서 `CONFIRMED` 로 세운다. 승인과 발주 확정은 다른
사실이지만, 발주 확정 단계에 코드가 없어(현실 순서에서 한 칸이 비었다) 승인을 그것으로
대신 본다. 물류가 발주 확정 단계를 만들면 승인 전이에서 그 칸을 뺀다.

   B-1(`tools.py` `find_in_transit_schedule_gap`)은 `in_transit` 의 `inbound_id` 를
   `confirmed_inbound_schedule` 에서 찾는다. 두 목록을 같은 `inbound_schedules` 에서
   만들기 때문에 승인분은 두 쪽에 함께 선다.

이 파일에는 승인 전이 기록의 순서(fixture 행 잠금 → status 두 칸 → 입고 일정)가 있다.
입고 예정 계산은 `domain/transition.py`, SQL 은 `repository/transition.py`, 등록소 표면은
`adapter.LogisticsTransitionAdapter` 다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from app.logistics.repository.purchase_detail import fetch_purchase_detail
from app.logistics.repository.transition import confirm_fixture_statuses, lock_fixture_row
from app.logistics.schemas.inbound_schedules import ScheduleReferenceMissing
from app.logistics.schemas.snapshot import InTransitItem
from app.logistics.schemas.transition import LogisticsFixtureMissing
from app.logistics.schemas.vocabulary import USAGE_SCOPE
from app.logistics.service.inbound_schedules import record_schedule


def persist_inventory(
    conn: Any,
    *,
    sim_run_id: str,
    as_of: date,
    rows: Sequence[InTransitItem],
    source_ref: str,
) -> None:
    """승인분을 `inbound_schedules` 에 적고 그날 fixture 행의 입고 status 를 세운다.

    commit 하지 않는다. 커밋은 재무 write 와 함께 마스터가 한 번 한다.
    여기서 커밋하면 물류만 먼저 확정되고, 뒤이어 재무가 실패했을 때 현금은 안
    나갔는데 입고 예정만 있는 장부가 남는다.

    자기 커넥션을 새로 열지 않는다. 여기서 커넥션을 만들면 마스터가 쥔
    트랜잭션 밖에서 쓰게 되어 위와 같은 반쪽 상태가 다시 생긴다. 인자로 받은
    `conn` 만 쓴다. 기존 일정을 읽는 SELECT 도 같은 커넥션 · 같은 트랜잭션에서 한다.

    fixture 행에서 건드리는 칸은 넷이다.

      ```text
      in_transit_status · confirmed_inbound_status   CONFIRMED 로 세운다 (Header 표시)
      source_ref · updated_at                        덮어쓴다
      ```

      두 JSON 칸을 쓰지 않는다 (W3-3). 업무 사실은 `inbound_schedules` 에만 적는다.
      status 를 세우는 이유는 Reader 가 `UNRESOLVED` 인 축의 목록을 숨기기 때문이다
      (`domain/snapshot.schedule_source`) — 세우지 않으면 방금 적은 일정이 안 보인다.
      `confirmed_inbound_status` 를 승인에서 세우는 이유는 모듈 docstring 참조.

      `confirmed_outbound_*` · `evidence_grade` · `approved_by` · `lot_priority_*` ·
      `zone_capacity_*` 는 다른 사실이고 다른 근거를 갖는다. 손대지 않는다.

    잠금 순서가 계약이다.

      ```text
      ① SELECT … FOR UPDATE   그 fixture 행 하나를 잠근다 (없으면 멈춘다)
      ② status 두 칸을 세운다
      ③ 입고 일정을 적는다      record_schedule — 같은 사실이면 no-op, 다른 사실이면 멈춘다
      ```

      같은 fixture 행을 겨냥한 승인은 이 행 잠금으로 직렬화된다. 행 잠금은 바깥
      트랜잭션이 커밋/롤백할 때까지 유지되고, 이 함수는 아무것도 직접 풀지 않는다.

      advisory lock 을 새로 만들지 않는다. 이 함수가 바꾸는 fixture 행은 이미 알고
      있는 행 하나이고, 그 행 자체가 경합 자원이다. 행 잠금으로 충분하고 추론하기도
      쉽다 (`ledger.py` 가 전역 advisory lock 을 쓰는 이유는 거기가 여러 행·여러 표를
      오가기 때문이라 사정이 다르다).

      직렬화되는 것은 같은 fixture 행뿐이다. 다른 `as_of` · 다른 `sim_run_id` 를
      겨냥한 승인은 서로 기다리지 않는다.

    업무 사실은 `inbound_schedules` 에만 적는다 (W3-3).

      ```text
      Header   in_transit_status · confirmed_inbound_status   여기서 CONFIRMED 로
      업무 사실 inbound_schedules 1행                          _record_schedules
      ```

      날짜별 복제를 하지 않는다. 일정 한 행이 날짜에 묶여 있지 않고 Reader 가
      날짜로 질의한다 — 미래 날짜 행이 먼저 열려도 그 승인을 못 보는 일이 없다
      (FIRSTINB 사고가 구조적으로 재현되지 않는 이유).

      같은 커넥션 · 같은 바깥 트랜잭션이다. status 와 일정이 한쪽만 커밋되면
      "확인했다는데 볼 것이 없는 날" 이 남는다. 이 함수가 커밋을 안 하는 것이
      그 보장의 전부다.

    :raises LogisticsFixtureMissing: 그날의 fixture 행이 없을 때. 만들지 않는다.
    :raises ScheduleConflict: 같은 `inbound_id` 가 다른 사실로 이미 있을 때
        (`record_schedule`). 마스터가 승인 전이 전체를 롤백할 수 있다.
    :raises ScheduleAlreadyCancelled: 그 일정이 이미 취소돼 있을 때.
    :raises ScheduleReferenceMissing: 행에 `purchase_id` 가 없어 신규 표의
        `purchase_item_id` 를 세울 수 없을 때.
    :raises PurchaseDetailMissing: 그 `purchase_id` 의 매입 줄이 없을 때.
    :raises PurchaseDetailAmbiguous: 매입 줄이 둘 이상일 때. 고르지 않는다.
    """
    missing = LogisticsFixtureMissing(
        # 무엇이 없는지 보이게 적는다. "행이 없다" 만으로는 sim_run_id 가 틀린
        # 것인지 그날 fixture 가 아직 안 만들어진 것인지 가릴 수 없다.
        "갱신할 물류 runtime fixture 행이 없다"
        f" (sim_run_id={sim_run_id}, as_of={as_of}, usage_scope={USAGE_SCOPE})."
        " 새 행을 만들지 않는다 — evidence_grade · approved_by · 나머지 두"
        " status 는 물류 판단이다."
    )

    if not lock_fixture_row(conn, sim_run_id=sim_run_id, as_of=as_of):
        # 그날 행이 없으면 승인이 앉을 자리가 없다 — UPDATE 전에 멈춘다.
        raise missing

    # `CONFIRMED` 로 세운다. 이 승인으로 그 축을 확인했기 때문이다.
    # `UNRESOLVED` 인 날을 그대로 두면 Reader 가 방금 적은 일정을 숨긴다.
    # 목록의 길이를 여기서 세지 않는다 — 길이는 Reader 가 신규 표에서 낸다.
    if confirm_fixture_statuses(
        conn, sim_run_id=sim_run_id, as_of=as_of, source_ref=source_ref
    ) != 1:
        raise missing

    # ── 업무 사실은 여기 하나에만 적는다 (W3-3) ─────────────────────────
    _record_schedules(conn, sim_run_id=sim_run_id, as_of=as_of, rows=rows, source_ref=source_ref)


def _record_schedules(
    conn: Any,
    *,
    sim_run_id: str,
    as_of: date,
    rows: Sequence[InTransitItem],
    source_ref: str,
) -> None:
    """같은 승인분을 `inbound_schedules` 에 적는다.

    `purchase_item_id` 를 새 조회로 얻지 않는다. 기존
    `purchase_detail.fetch_purchase_detail` 을 그대로 부른다 — 그 함수가 이미
    `0행 Missing · 1행 정상 · 2행 이상 Ambiguous` 를 가르고, 같은 판정을 두 곳에
    두면 한쪽만 고쳐지는 날이 온다.

    부모 행은 같은 트랜잭션 안에 이미 서 있다. 마스터 `apply_approval` 이
    `persist_purchases` → `finance.persist` → `logistics.persist` 순으로 부르므로
    (`app/master/service/transition.py` 주석), 여기서 `purchase_items` 를 읽을 수 있다.

    `purchase_id` 가 없으면 멈춘다. 비워 두고 넘어가지 않는다.
    이 표가 도착 조회의 정본이라(W3-2) 빠진 행은 "승인은 났는데 도착 조회에 안
    잡히는 입고" 가 된다 — FIRSTINB 사고와 같은 모양이다.

    수량을 매입 줄과 대조하지 않는다. `quantity_kg` 는 회차 수량이고 매입 줄은
    회차들의 합일 수 있다. 실측 5/5 가 같은 것은 지금 분할 회차가 없어서지
    계약이 아니다 — 없는 규칙을 여기서 만들지 않는다.
    """
    for item in rows:
        if not item.purchase_id:
            raise ScheduleReferenceMissing(
                f"입고 일정에 매입 참조가 없다 (sim_run_id={sim_run_id!r},"
                f" inbound_id={item.inbound_id!r}, as_of={as_of})."
                " purchase_item_id 를 세울 수 없어 신규 표에 적을 수 없다 —"
                " 비워 두면 W3-2 에서 그 입고가 도착 조회에서 사라진다."
                " 이 값은 마스터가 만든다 (app/master/transition.py purchase_id_for)."
            )
        if item.inbound_id is None or item.expected_arrival_date is None:
            raise ScheduleReferenceMissing(
                f"입고 일정에 열쇠나 도착일이 없다 (sim_run_id={sim_run_id!r},"
                f" inbound_id={item.inbound_id!r},"
                f" expected_arrival_date={item.expected_arrival_date})."
                " 도착 조회 축이 서지 않는다 — 지어내지 않는다."
            )
        # 0 / 1 / 2행 이상 판정은 저쪽이 한다. 여기서 다시 세지 않는다.
        detail = fetch_purchase_detail(conn, purchase_id=item.purchase_id)
        record_schedule(
            conn,
            sim_run_id=sim_run_id,
            inbound_id=item.inbound_id,
            purchase_item_id=detail.purchase_item_id,
            quantity_kg=item.quantity_kg,
            expected_arrival_date=item.expected_arrival_date,
            # 승인 전이가 겨냥한 그날이 곧 이 일정이 장부에 선 날이다.
            created_as_of=as_of,
            source_ref=source_ref,
        )
