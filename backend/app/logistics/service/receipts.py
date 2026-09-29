"""receipts.py — 입고 Receipt 행의 **존재와 현재 상태**를 묻는다 (3-B4-C · 3-B4-F).

```text
arrival.select_due_inbound   →  due 행    물류가 아는 사실만으로 자격이 있나
receipts.check_receipt_state →  NEW                이 입고에 Receipt 행이 아직 없다
                                ALREADY_EXISTS     Receipt 행이 이미 존재한다
                                + receipt_status   그 행이 지금 어느 상태인가
```

🔴 **`ALREADY_EXISTS` 를 *"입고 처리가 끝났다"* 로 읽으면 안 된다.**

   ```text
   Receipt 가 ARRIVED 로 있다
   검수 없음 · Lot 없음 · 원장 IN 없음
   ⇒ 행은 있지만 **처리는 끝나지 않았다**
   ```

   그 상태로 건너뛰면 **Receipt 만 남고 재고가 안 들어온 채 영구 고착된다.**
   그래서 3-B4-F 에서 `receipt_status` 를 함께 내보낸다 — 뒤 단계가 가를 수 있게.

⚠️ **어느 상태에서 무엇으로 이어갈지는 여기서 정하지 않는다.** 그 상태기계는 별도
   감사가 정한다 (스키마에 전이 규칙이 없고 씨앗 행도 0건이라 지금 근거로는 증명할
   수 없다 — 3-B4-D 감사 K 항목). 이 파일은 *"실제 상태가 무엇인가"* 만 답한다.

★ **`arrival.py` 와 나눈 이유가 있다.** 저쪽은 **순수 계산**이고 그 순수성을
  테스트가 잠그고 있다(임포트 목록까지 고정한다). 이 파일은 DB 를 읽으므로 섞으면
  그 방어가 통째로 무너진다 — 분류는 저쪽, 조회는 이쪽이다.

🔴 **`repository.py` 에 두지 않았다.** 그쪽은 `db.fetch_all` 로 **자기 커넥션을 연다.**
   (2026-09-30 재구성 BL-015 전 이야기다 — 지금은 `repository/` 전체가 받은 연결로만 읽는다.)
   이 조회는 나중에 Receipt·검수·Lot·원장 IN 과 **한 트랜잭션**에 들어가야 해서,
   호출자가 준 커넥션만 써야 한다 (`ledger.py` · `transition.persist_inventory` 가
   같은 이유로 `repository` 를 안 쓴다).

🟢 **3-B4-G 부터 이 파일이 Receipt 를 만든다.** 쓰기는 딱 하나뿐이다 —
   `ARRIVED` 행 INSERT. **UPDATE · DELETE 는 없고 앞으로도 이 단계에 없다.**

   ```text
   check_receipt_state    읽기 전용 — 계속 아무것도 안 쓴다
   create_arrived_receipt 잠금 → 재조회 → NEW 일 때만 INSERT 한 번
   ```

⚠️ **읽기만으로는 동시 중복 생성을 막지 못한다 — 그래서 잠금을 붙였다.**

   ```text
   T1  조회 → 0건 (NEW)
   T2  조회 → 0건 (NEW)      ← 둘 다 "새 건" 으로 본다
   T1  INSERT
   T2  INSERT                 여기서야 부딪힌다
   ```

   🟢 **`create_arrived_receipt` 가 그 틈을 닫는다** — 도착 쓰기 **전역** advisory
      xact lock 을 먼저 잡고 **잠금 안에서 다시 조회한다.** 잠금 밖에서 이미 한
      조회를 믿지 않는다.

   🔴 **입고별 잠금을 쓰지 않는다.** `ledger._lock_ledger_writes` 가 적어 둔 교착이
      그대로 재현되기 때문이다 — 한 트랜잭션이 여러 입고를 처리하면 두 트랜잭션이
      **요청하는 잠금 집합 자체가 달라** 전순서를 매길 수 없다. 전역 하나로 합치면
      기다리는 쪽이 아직 아무 자원도 안 쥐고 있어 순환이 생길 자리가 없다.

   ⚠️ **UniqueViolation 을 정상 흐름으로 쓰지 않는다** (`ledger.py` 와 같은 규율).
      DB 무결성 예외는 트랜잭션을 aborted 로 만들어 바깥이 롤백할 수밖에 없게 한다 —
      *"이미 있으니 넘어간다"* 를 그것으로 표현하면 멀쩡한 재실행이 장애가 된다.
      `uq_inbound_receipts_inbound_id` 는 **최종 안전망**으로 남는다. 잠금이 있는데도
      그 그물이 터지면 그것은 버그이므로 **삼키지 않고 그대로 올린다.**

★ **스키마 실측 (2026-09-05 · 현재 브랜치 `database/30_logistics_wms_schema.sql`).**

  ```text
  PRIMARY KEY   inbound_receipts_pkey (receipt_id)
  UNIQUE        uq_inbound_receipts_inbound_id (sim_run_id, inbound_id)
  sim_run_id    TEXT NOT NULL   FK → sim_runs
  inbound_id    TEXT            🔴 nullable
  receipt_id    TEXT NOT NULL
  ```

  🔴 **`inbound_id` 가 nullable 이라 UNIQUE 가 완전하지 않다.** PostgreSQL 은 UNIQUE
     에서 NULL 을 서로 다른 값으로 보므로 `inbound_id IS NULL` 인 행은 몇 개든 선다.
     그래서 이 조회는 **빈 식별자를 아예 받지 않는다** — 없는 열쇠로 물으면
     *"0건이니 새 건"* 이라는 틀린 답이 나오고, 뒤 단계가 그 쓰레기 식별자로 행을
     만들어 UNIQUE 축을 오염시킨다.

★ 2026-09-30 재구성 BL-015: `logistics/receipts.py` 을 계층별로 나눴다. 이 파일에는 Receipt 조회 ·
  도착 기록의 **순서**가 남았다. 판정은
  `domain/receipts.py`, SQL 은 `repository/receipts.py`, 도착 잠금은 `repository/locks.py`.
"""

from __future__ import annotations

from typing import Any

from app.logistics.domain.arrival import DueInbound
from app.logistics.domain.receipts import (
    check_arrival_facts,
    check_receipt_keys,
    receipt_existence,
    receipt_id_for,
)
from app.logistics.repository.locks import lock_arrival_writes
from app.logistics.repository.receipts import insert_arrived_receipt, select_receipt_rows
from app.logistics.schemas.purchase_detail import PurchaseDetail
from app.logistics.schemas.receipts import STATUS_ON_ARRIVAL, ReceiptExistence, ReceiptWriteResult


def check_receipt_state(
    conn: Any,
    *,
    sim_run_id: str,
    inbound_id: str,
) -> ReceiptExistence:
    """이 입고 건에 Receipt 가 이미 있는가. **읽기만 한다.**

    ```text
    0건      NEW              Receipt 행이 아직 없다 — 뒤 단계가 만들 수 있다
                              receipt_id=None · receipt_status=None
    1건      ALREADY_EXISTS   Receipt 행이 **이미 존재한다** — 멱등 재실행이다
                              receipt_id · receipt_status 를 DB 값 그대로 싣는다
    2건 이상  ReceiptIntegrityError
    ```

    🔴 **`ALREADY_EXISTS` 는 "입고 처리가 끝났다" 가 아니다.** Receipt 행 하나가
       있다는 사실뿐이고, 그 행이 `ARRIVED` 인데 검수·Lot·원장 IN 이 없을 수 있다.
       그래서 `receipt_status` 를 함께 돌려준다 — 뒤 단계가 그것을 보고 갈라야 한다.

    ⚠️ **여기서 진행 여부를 정하지 않는다.** 어느 상태에서 무엇으로 이어갈지는
       별도의 상태기계 감사가 정한다. 이 함수는 *"실제 상태가 무엇인가"* 만 답한다.

    🔴 **조회 열쇠는 `(sim_run_id, inbound_id)` 다.** DB 의 유일성 축과 **같아야**
       한다 — 다른 축으로 물으면 *"있다/없다"* 와 *"두 번 못 선다"* 가 서로 다른
       것을 뜻하게 된다.

    🔴 **`purchase_id` · `approval_id` · 품목명 · 도착예정일로 찾지 않는다.**
       그것들은 Receipt 의 정체성이 아니다. 매입 참조로 찾으면 회차가 여럿인 매입
       하나에 여러 입고가 달릴 때 남의 건을 자기 것으로 본다.

    ★ **`ALREADY_EXISTS` 는 실패가 아니다.** `ledger.record_inventory_move` 가 같은
      `move_id` 를 다시 받았을 때 `applied=False` 로 돌려주는 것과 같은 자리다 —
      멱등 재실행은 정상이고, 그 사실을 예외로 표현하면 바깥이 롤백하게 된다.
      **행이 있다는 이유만으로 예외를 올리지 않는다.**

    ⚠️ **찾은 Receipt 를 고치지 않는다.** 일정(`in_transit` · `confirmed_inbound`)도
       건드리지 않는다. 이 함수는 아무것도 쓰지 않는다.

    🔴 **커밋도 롤백도 하지 않고 커넥션을 새로 열지 않는다.** 받은 `conn` 만 쓴다 —
       나중에 Receipt·검수·Lot·원장 IN 이 **한 바깥 트랜잭션**으로 묶여야
       하고, 그 커밋은 호출자가 한 번 한다.

    :param conn: 호출자가 소유한 커넥션. 이 함수는 수명을 관리하지 않는다.
    :param sim_run_id: 어느 실행의 장부인가. **마스터가 정하는 값**이다.
    :param inbound_id: 물류 입고 정체성(`INB-{approval_id}-{seq}`). 비어 있으면 안 된다.
    :raises InvalidInboundIdentity: `inbound_id` 가 비었거나 공백뿐일 때.
    :raises ReceiptIntegrityError: 같은 열쇠에 Receipt 가 둘 이상일 때.
    :raises ReceiptRowUnreadable: 찾은 행의 `receipt_id` 가 비었거나
        `receipt_status` 가 DB CHECK 어휘 밖일 때. **대체값으로 읽지 않는다.**
    """
    # ★ **DB 에 묻기 전에 막는다.** `inbound_id IS NULL` 이나 빈 문자열로 조회하면
    #   0건이 돌아오고, 그 0건은 "아직 없다" 로 읽힌다 (`InvalidInboundIdentity`).
    check_receipt_keys(sim_run_id=sim_run_id, inbound_id=inbound_id)

    rows = select_receipt_rows(conn, sim_run_id=sim_run_id, inbound_id=inbound_id)

    return receipt_existence(rows, sim_run_id=sim_run_id, inbound_id=inbound_id)


def create_arrived_receipt(
    conn: Any,
    *,
    sim_run_id: str,
    inbound: DueInbound,
    purchase_detail: PurchaseDetail,
) -> ReceiptWriteResult:
    """도착한 입고 한 건을 `ARRIVED` Receipt 로 적는다. **멱등하다.**

    ```text
    ① 입력 검증                     DB 를 안 만진다
    ② 도착 쓰기 전역 advisory lock   여기서부터 이 입고를 다루는 것은 나 하나다
    ③ 잠금 안에서 **다시** 조회      ★ 잠금 밖의 조회를 믿지 않는다
       ├ 있음  applied=False, DB 값 그대로 → INSERT 없음
       └ 없음  ↓
    ④ INSERT (ARRIVED · SCENARIO_SIMULATED)
    ```

    🔴 **③ 이 ② 뒤인 것이 이 함수의 동시성 계약이다.** 호출자가 앞서
       `check_receipt_state` 를 불렀더라도 그 답은 잠금 **밖**의 사실이라 이미
       낡았을 수 있다. 다시 묻지 않으면 두 트랜잭션이 함께 *"새 건"* 을 보고 둘 다
       INSERT 로 간다.

    ★ **읽는 사실은 전부 앞 단계가 검증한 것이다.** 여기서 매입을 조회하지도,
      `purchase_id` 를 뜯지도, 품목명을 번역하지도, 단가를 계산하지도 않는다 —
      `DueInbound` 와 `PurchaseDetail` 이 이미 그 일을 마쳤다.

    🔴 **`arrived_at = inbound.expected_arrival_date` 다.**
       `date.today()` · `as_of` · `CURRENT_DATE` · `created_at` 을 쓰지 않는다.

    ```text
    expected_arrival_date = 2026-01-05
    처리 as_of             = 2026-01-07
    arrived_at            = 2026-01-05     ★ 연체분도 원래 예정일을 지킨다
    ```

       ⚠️ **이것은 "그날 물리적으로 도착한 것을 관측했다" 는 주장이 아니다.**
          `SCENARIO_SIMULATED` 에는 별도의 실제 도착일 원천이 없어, 계획된 예정일을
          **모의 도착일로 쓴다.** 그 사실은 `fact_source` 가 이미 기록한다.
          `as_of` 로 옮기면 그 로트가 이틀 더 신선한 것처럼 보이고, 신선도는 폐기·판매
          판단으로 흘러간다.

    ⚠️ **일정 수량과 매입 수량을 대조하지 않는다 (3-B4-E 결론).** 두 값은 같은
       `leg.qty_kg` 에서 오지만 가공이 다르다 — 매입은 6자리로 quantize 하고 물류는
       원값을 유지해서, 소수 6자리를 넘으면 **정당하게 달라진다.** 임의의 허용오차를
       지어내지 않고, `ordered_qty_kg` 는 **권위 있는 매입 사실**을 그대로 쓴다.
       대조 규칙은 나중의 통합 불변식으로 남긴다.

    🔴 **커밋도 롤백도 하지 않고 커넥션을 새로 열지 않는다.** 잠금도 트랜잭션 수명이라
       호출자의 커밋/롤백과 함께 풀린다.

    ⚠️ **일정(`in_transit` · `confirmed_inbound`)을 건드리지 않는다.** 그래서 fixture
       행을 `FOR UPDATE` 로 잡지도 않는다 — 이 판은 일정을 바꾸지 않는다.

    :param conn: 호출자가 소유한 커넥션. 이 함수는 수명을 관리하지 않는다.
    :param sim_run_id: 어느 실행의 장부인가. **마스터가 정하는 값**이다.
    :param inbound: `arrival.select_due_inbound` 이 `due` 로 가른 행.
    :param purchase_detail: `purchase_detail.fetch_purchase_detail` 이 읽은 매입 줄.
    :raises InvalidInboundIdentity: 식별자가 비었거나 공백뿐일 때.
    :raises ReceiptFactsMissing: 매입 상세의 필수 값이 비었을 때.
    :raises ReceiptIntegrityError: 같은 열쇠에 Receipt 가 둘 이상일 때.
    :raises ReceiptRowUnreadable: 기존 행의 값을 계약대로 읽을 수 없을 때.
    """
    # ── ① 입력 검증 — DB 를 만나기 전에 끝낸다 ──────────────────────────
    receipt_id = receipt_id_for(sim_run_id=sim_run_id, inbound_id=inbound.inbound_id)
    check_arrival_facts(inbound=inbound, purchase_detail=purchase_detail)

    # ── ② 잠금이 먼저다 ────────────────────────────────────────────
    lock_arrival_writes(conn)

    # ── ③ 잠금 안에서 다시 묻는다 ──────────────────────────────────────
    existing = check_receipt_state(conn, sim_run_id=sim_run_id, inbound_id=inbound.inbound_id)
    if existing.status == "ALREADY_EXISTS":
        # ★ 타입 좁히기 — `ALREADY_EXISTS` 면 두 값이 다 있다 (`check_receipt_state`
        #   이 비거나 어휘 밖인 값을 이미 막는다).
        assert existing.receipt_id is not None
        assert existing.receipt_status is not None
        # 🔴 **고치지 않는다.** 상태를 진행시키지도, 일정을 건드리지도 않는다.
        #    `receipt_id` 는 우리가 지은 값이 아니라 **DB 에 적힌 값**을 돌려준다 —
        #    이 규칙이 생기기 전에 만들어진 행이라도 그 행이 진짜다.
        return ReceiptWriteResult(
            applied=False,
            receipt_id=existing.receipt_id,
            receipt_status=existing.receipt_status,
        )

    # ── ④ 없을 때만 쓴다 ──────────────────────────────────────────────
    insert_arrived_receipt(
        conn,
        receipt_id=receipt_id,
        sim_run_id=sim_run_id,
        inbound_id=inbound.inbound_id,
        expected_arrival_date=inbound.expected_arrival_date,
        purchase_detail=purchase_detail,
    )

    return ReceiptWriteResult(
        applied=True,
        receipt_id=receipt_id,
        receipt_status=STATUS_ON_ARRIVAL,
    )
