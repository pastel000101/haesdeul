"""transition.py — 승인 약정을 **물류 재고 상태로 옮기는** build·persist (C 형태 ⑦).

마스터가 `app/master/transition.py` 에서 트랜잭션 경계를 쥐고, **무슨 값을 어느 칸에
어떤 SQL 로 쓸지는 물류가 소유한다.** 이 파일이 그 물류 몫이다.

```text
승인 → ApprovedCommitment → build_next_inventory  (순수 계산 · DB 를 안 부른다)
                          → persist_inventory     (주어진 conn 으로 write · commit 안 함)
```

🔴 **왜 `inventory_lots` 가 아니라 runtime fixture 인가.**
   승인 시점의 입고 예정을 `inventory_lots` 에 넣을 수 없다 (2026-09-03 실측).
   네 가지가 막는다.

   ```text
   status CHECK       ACTIVE · DEPLETED · DISPOSED · HOLD   IN_TRANSIT 이 없다
   move_type CHECK    IN · OUT · DISPOSE · ADJUST           입고 예정 값이 없다
   purchase_item_id   NOT NULL + FK → purchase_items        승인만으론 그 행이 없다
   unit_cost · zone   NOT NULL                              승인 시점에 없거나 추정이다
   ```

   ★ 네 가지 중 어느 하나도 **코드로 우회할 수 없다.** 상태값을 지어내면 CHECK 가
     막고, 막지 않게 스키마를 열면 *"아직 안 온 물건"* 이 실재 로트와 같은 칸에 앉는다.
     `unit_cost` 를 추정으로 채우면 그 추정이 원가가 되어 재무로 흘러간다.

🟢 **입고 예정의 정본은 `inbound_schedules` 다 (W3-3 · 2026-09-09).**
   승인이 그 표에 한 행을 적고, Reader 가 날짜로 질의한다 — 날짜별 fixture JSON 에
   복제하지 않는다. 계약은 `schemas.py` 의 `InTransitItem` 그대로이고,
   `in_transit_status` 는 이제 **Header 표시**(그 축을 확인했나)로만 남는다.

🟡 **매입 참조(`purchase_id`)를 받을 자리는 뚫려 있고, 마스터는 그것을 넘기지 않는다.**
   운송 중인 물건이 도착하면 물류는 그 매입 줄에서 `purchase_item_id` · `item_id` ·
   `grade` · `unit_price_krw_per_kg` 를 읽는다. 그 참조를 **물류가 지어내면 안 되고**,
   만드는 곳은 마스터다 (`app/master/transition.py` 의 `purchase_id_for`).

   ```text
   마스터 경로   logistics.build(commitment, target_state_date=…)
                 → purchase_ids=None → purchase_id=None          ★ 확정된 정상 상태다
   값을 주는 호출 logistics.build(…, purchase_ids={leg.seq: purchase_id})
                 → purchase_ids[leg.seq] 를 그대로 보관
   ```

   🔴 **마스터 규약이 이 인자를 빼기로 확정했다.** `LogisticsTransition.build` 는
      마스터 소유 파일에 있고, 그쪽 주석이 못박고 있다 —
      *"`purchase_ids` 를 받지 않는다. 물류가 쓰는 `in_transit` 은 `purchases` 를
      참조하지 않는다 — 필요 없는 값을 규약에 얹지 않는다."*
      그래서 `purchase_ids=None` 은 **미결이 아니라 확정된 계약**이다.

   ★ **그래도 인자를 지우지 않는다.** 기본값 `None` 인 선택 경로를 남겨 두면, 참조를
     아는 호출자가 생겼을 때 물류 쪽을 고치지 않고 값만 실어 보낼 수 있다. 없애면
     그날 이 파일을 다시 열어야 한다.

   ⚠️ **참조가 없는 행은 도착일에 `blocked` 로 드러난다.** `arrival.select_due_inbound`
      가 `purchase_id` 없는 행을 `due` 로 넘기지 않는다. 조용히 통과시키지 않으므로
      *"참조 없이 만들어진 행"* 이 로트가 되는 일은 없다.

🟢 **`in_transit` → 실제 입고(`inventory_lots`) 전환은 이제 구현돼 있다.**

   ```text
   arrival.select_due_inbound       도착 자격 판정 (eta · inbound_id · purchase_id)
   purchase_detail.fetch_...        매입 줄 조회 — 등급·단가의 권위 출처
   receipts.create_arrived_receipt  ARRIVED Receipt
   inspections.record_inspection    검수 → INSPECTED
   inbound_stock.materialize_...    accepted 수량으로 Lot 생성 + 원장 IN → PUTAWAY_DONE
   ```

   ★ **이 파일은 그 경로에 관여하지 않는다.** 여기가 하는 일은 승인 시점의 입고 예정을
     runtime fixture 에 적는 것까지이고, 그 뒤는 위 모듈들이 각자 소유한다.

⚠️ **`confirmed_inbound` 를 같이 쓰는 것은 임시 조치다 (2026-09-04).**

   ```text
   ① 왜 임시인가   승인과 발주 확정은 다른 사실이다. 지금은 발주 확정 단계에
                    코드가 없어(현실 순서에서 한 칸이 비었다) 승인을 그것으로
                    대신 본다
   ② 언제 걷나     물류가 발주 확정 단계를 만들면 이 병합을 걷어낸다 —
                    `persist_inventory` 에서 `confirmed_inbound_*` 두 칸만 빼면 된다
   ③ 무엇을 지켰나 물류가 경고한 *"승인 반영이 조용히 남의 칸을 덮는다"* 를
                    **덮어쓰기가 아니라 병합**으로 피했다
   ```

   ★ 그 한 칸이 비어 있어서 관통 Day2 가 섰다. B-1(`tools.py`
     `find_in_transit_schedule_gap`)이 `in_transit` 의 `inbound_id` 를
     `confirmed_inbound_schedule` 에서 찾는데, 그 칸에 쓰는 운영 코드가 아무 데도
     없어 `IN_TRANSIT_NOT_IN_CONFIRMED_SCHEDULE` 가 나온다. **B-1 규칙은 맞다** —
     고쳐야 할 쪽은 값을 안 채우는 이쪽이다.

★ 2026-09-30 재구성 BL-015: `logistics/transition.py` 을 계층별로 나눴다. 이 파일에는 승인 전이
  기록의 **순서**가 남았다(fixture 행 잠금 →
  status 두 칸 → 입고 일정). 입고 예정 계산은 `domain/transition.py`, SQL 은
  `repository/transition.py`, 등록소 표면은 `adapter.LogisticsTransitionAdapter`.
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
    """계산된 입고 예정을 runtime fixture 의 `in_transit` · `confirmed_inbound` 에 쓴다.

    🔴 **commit 하지 않는다.** 커밋은 재무 write 와 함께 마스터가 한 번 한다.
       여기서 커밋하면 물류만 먼저 확정되고, 뒤이어 재무가 터졌을 때 **현금은 안
       나갔는데 입고 예정만 있는 장부**가 남는다.

    🔴 **자기 커넥션을 새로 열지 않는다.** 여기서 커넥션을 만들면 마스터가 쥔
       트랜잭션 밖에서 쓰게 되어 위와 같은 반쪽 상태가 다시 생긴다. 인자로 받은
       `conn` 만 쓴다 — 이 모듈이 `repository.py` 의 `fetch_all` 을 안 쓰는 이유다.
       기존 목록을 읽는 SELECT 도 같은 커넥션 · 같은 트랜잭션에서 한다.

    ⚠️ **`confirmed_inbound_*` 두 칸은 임시로 얹은 것이다** (모듈 docstring 참조).
       승인과 발주 확정은 다른 사실이고, 지금은 발주 확정 단계에 코드가 없어 승인을
       그것으로 대신 본다. **물류가 그 단계를 만들면 이 두 칸을 여기서 걷어낸다.**

    ★ 건드리는 칸은 여섯이다.

      ```text
      in_transit_status · confirmed_inbound_status   CONFIRMED 로 세운다 (Header 표시)
      source_ref · updated_at                        덮어쓴다
      ```

      🔴 **두 JSON 칸을 안 쓴다 (W3-3).** 업무 사실은 `inbound_schedules` 에만 적는다.
         status 만 세우는 이유는 Reader 가 `UNRESOLVED` 인 축의 목록을 숨기기 때문이다
         (`domain/snapshot.schedule_source`) — 안 세우면 방금 적은 일정이 안 보인다.

      🔴 **`in_transit` 도 병합으로 바뀌었다.** 종전에는 덮어썼고, 같은 fixture 행을
         겨냥한 승인이 둘이면 뒤엣것이 앞엣것을 **에러 없이 지웠다**
         (`_merge_in_transit` 에 그 시나리오를 적었다).

      `confirmed_outbound_*` · `evidence_grade` · `approved_by` · `lot_priority_*` ·
      `zone_capacity_*` 는 **다른 사실이고 다른 근거를 갖는다.** 손대지 않는다.

    ★ **B-1 은 이 함수가 세운다.** 두 칸이 같은 승인분을 `_merge_schedule` 로 똑같이
      받으므로, 이번에 쓴 in_transit 행마다 confirmed 에 같은 `inbound_id` ·
      `item` · `quantity_kg` · 날짜의 짝이 선다. 앞선 승인분도 같은 경로로 들어왔기에
      두 목록이 함께 자란다.

    🔴 **읽고-고치고-쓰는 한 덩어리다. 잠금 순서가 곧 계약이다.**

      ```text
      ① SELECT … FOR UPDATE   그 fixture 행 하나를 잠근다
      ② 현재 두 목록을 읽는다
      ③ 검증하고 병합한다      (파이썬에서 — 여기가 비어 있으면 경합이 끼어든다)
      ④ 같은 행을 UPDATE 한다
      ```

      ★ **행 잠금은 바깥 트랜잭션이 커밋/롤백할 때까지 유지된다.** 이 함수는 아무것도
        직접 풀지 않는다 — 풀 수 있으면 ③ 과 ④ 사이가 다시 열린다.

      🔴 **잠금 없이 병합하면 마지막 쓴 쪽이 이긴다 (lost update).**

      ```text
      초기            in_transit = [A]
      T1 승인 B        SELECT → [A]        병합 → [A, B]
      T2 승인 C        SELECT → [A]        병합 → [A, C]   ← 같은 옛 목록을 읽었다
      T1 UPDATE·COMMIT                    [A, B]
      T2 UPDATE·COMMIT                    [A, C]           🔴 승인 B 가 사라진다
      ```

      ⚠️ **3-B1 의 병합만으로는 이것을 못 막는다.** 병합은 *"한 트랜잭션이 본 목록"*
         위에서만 정확하고, 두 트랜잭션이 같은 옛 목록을 보는 것 자체를 막지 못한다.
         B-1 도 못 잡는다 — 사라진 쪽이 두 칸에서 **함께** 빠져 대조가 성립한다.

      ★ **advisory lock 을 새로 만들지 않았다.** 이 함수가 바꾸는 것은 **이미 알고 있는
        행 하나**이고, 그 행 자체가 경합 자원이다. 행 잠금으로 충분하고 추론하기도 쉽다
        (`ledger.py` 가 전역 advisory lock 을 쓰는 이유는 거기가 **여러 행·여러 표**를
        오가기 때문이라 사정이 다르다).

      ⚠️ **직렬화되는 것은 같은 fixture 행뿐이다.** 다른 `as_of` · 다른 `sim_run_id` 를
         겨냥한 승인은 서로 기다리지 않는다.

    ★ **업무 사실은 `inbound_schedules` 에만 적는다 (W3-3).**

      ```text
      Header   in_transit_status · confirmed_inbound_status   여기서 CONFIRMED 로
      업무 사실 inbound_schedules 1행                          _record_schedules
      ```

      🔴 **날짜별 복제를 안 한다.** 일정 한 행이 날짜에 안 묶여 있고 Reader 가
         날짜로 질의한다 — 미래 날짜 행이 먼저 열려도 그 승인을 못 보는 일이 없다
         (FIRSTINB 사고가 구조적으로 재현되지 않는 이유).

      🔴 **같은 커넥션 · 같은 바깥 트랜잭션이다.** status 와 일정이 한쪽만 커밋되면
         *"확인했다는데 볼 것이 없는 날"* 이 남는다. 이 함수가 커밋을 안 하는 것이
         그 보장의 전부다.

    :raises LogisticsFixtureMissing: 그날의 fixture 행이 없을 때. **만들지 않는다.**
    :raises InboundScheduleConflict: 같은 `inbound_id` 가 다른 사실로 이미 있거나,
        기존 목록에 같은 id 가 둘 이상일 때. **DML 전에 오른다** — 마스터가 승인 전이
        전체를 롤백할 수 있다.
    :raises ScheduleReferenceMissing: 행에 `purchase_id` 가 없어 신규 표의
        `purchase_item_id` 를 세울 수 없을 때.
    :raises PurchaseDetailMissing: 그 `purchase_id` 의 매입 줄이 없을 때.
    :raises PurchaseDetailAmbiguous: 매입 줄이 둘 이상일 때. **고르지 않는다.**
    """
    missing = LogisticsFixtureMissing(
        # ★ 무엇이 없는지 보이게 적는다. "행이 없다" 만으로는 sim_run_id 가 틀린
        #   것인지 그날 fixture 가 아직 안 만들어진 것인지 가릴 수 없다.
        "갱신할 물류 runtime fixture 행이 없다"
        f" (sim_run_id={sim_run_id}, as_of={as_of}, usage_scope={USAGE_SCOPE})."
        " 새 행을 만들지 않는다 — evidence_grade · approved_by · 나머지 두"
        " status 는 물류 판단이다."
    )

    if not lock_fixture_row(conn, sim_run_id=sim_run_id, as_of=as_of):
        # ★ 그날 행이 없으면 승인이 앉을 자리가 없다 — UPDATE 전에 멈춘다.
        raise missing

    # 🔴 **`CONFIRMED` 로 세운다.** 이 승인으로 그 축을 확인했기 때문이다.
    #    `UNRESOLVED` 인 날을 그대로 두면 Reader 가 방금 적은 일정을 숨긴다.
    #    목록의 길이를 여기서 세지 않는다 — 길이는 Reader 가 신규 표에서 낸다.
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
    """같은 승인분을 신규 `inbound_schedules` 에도 적는다.

    🔴 **`purchase_item_id` 를 새 조회로 얻지 않는다.** 기존
       `purchase_detail.fetch_purchase_detail` 을 그대로 부른다 — 그 함수가 이미
       `0행 Missing · 1행 정상 · 2행 이상 Ambiguous` 를 가르고, 같은 판정을 두 곳에
       두면 한쪽만 고쳐지는 날이 온다.

    ★ **부모 행은 같은 트랜잭션 안에 이미 서 있다.** 마스터 `apply_approval` 이
      `persist_purchases` → `finance.persist` → `logistics.persist` 순으로 부르므로
      (`app/master/transition.py` 주석), 여기서 `purchase_items` 를 읽을 수 있다.

    🔴 **`purchase_id` 가 없으면 멈춘다. 비워 두고 넘어가지 않는다.**
       이 표는 W3-2 에서 정본이 되고, 그때 빠진 행은 *"승인은 났는데 도착 조회에
       안 잡히는 입고"* 가 된다 — FIRSTINB 사고와 같은 모양이다.

    ⚠️ **수량을 매입 줄과 대조하지 않는다.** `quantity_kg` 는 회차 수량이고 매입 줄은
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
        # ★ 0 / 1 / 2행 이상 판정은 저쪽이 한다. 여기서 다시 세지 않는다.
        detail = fetch_purchase_detail(conn, purchase_id=item.purchase_id)
        record_schedule(
            conn,
            sim_run_id=sim_run_id,
            inbound_id=item.inbound_id,
            purchase_item_id=detail.purchase_item_id,
            quantity_kg=item.quantity_kg,
            expected_arrival_date=item.expected_arrival_date,
            # 🔴 승인 전이가 겨냥한 그날이 곧 이 일정이 장부에 선 날이다.
            created_as_of=as_of,
            source_ref=source_ref,
        )
