"""물류 쓰기 전역 잠금 네 종류와 획득 순서 표. 잠금은 트랜잭션 수명(`pg_advisory_xact_lock`)이라
호출자의 commit · rollback 과 함께 풀린다 — 여기서 unlock 하지 않는다.

```text
좌표           대상                    함수                     부르는 service
(20260905, 1)  재고 원장 쓰기          lock_ledger_writes       ledger
(20260905, 2)  도착 Receipt 쓰기       lock_arrival_writes      receipts · inspections ·
                                                                inbound_stock · reconciliation
(20260905, 3)  출고 예약·할당 쓰기      lock_outbound_writes     outbound · fefo_allocation ·
                                                                disposal · maintenance
(20260905, 4)  Pallet 배치 쓰기        lock_warehouse_writes    warehouse
```

흐름별 획득 순서 (바꾸면 교착이 재현된다):

```text
입고(도착 처리)   ② 도착 → fixture 행 FOR UPDATE → Receipt · 검수 → ① 원장 → Lot 행 FOR UPDATE
출고             ③ 출고 → 가용량 재계산 → 예약 · 할당 쓰기 → ① 원장 → Lot 행 FOR UPDATE
자동 유지보수     ③ 출고 → (폐기) ① 원장 → Lot 행 → ④ 배치
배치             ④ 배치만
```

두 전순서(입고 · 출고)는 ① 에서만 만나고 순환이 없다. ① 을 fixture 행보다 먼저 잡는 경로를
만들지 않는다.

잠금 함수는 커서 대신 연결을 받는다 — 부르는 쪽이 `with conn.cursor()` 를 열지 않는다.
"""

from __future__ import annotations

from typing import Any

from psycopg import sql

#: 재고 원장 쓰기 잠금(`LOGISTICS_LEDGER_WRITE_LOCK`)의 좌표. 기술적 동시성 잠금이지
#: 업무 정책값이 아니다 — 그래서 DB(`agent_policy_config`)에 두지 않고 여기 상수로 둔다.
#:
#: 두-정수 형태(`pg_advisory_xact_lock(classid, objid)`)를 쓰는 이유: advisory lock 은
#: DB 전체가 나눠 쓰는 64비트 공간이라, 한-정수 형태로 쓰면 다른 서브시스템의 잠금과
#: 숫자가 겹칠 수 있다. classid 를 물류 전용으로 고정하면 겹침이 우리 안에서만 일어난다.
#: 주의: 다른 파트가 advisory lock 을 쓰게 되면 이 숫자를 피해야 한다 — 그래서 여기 적어 둔다.
#: 원장 잠금은 하나뿐이라 `move_id` 를 해싱할 일이 없다 — objid 도 고정값이다.
LEDGER_LOCK_CLASSID = 20260905
LEDGER_LOCK_OBJID = 1


def lock_ledger_writes(conn: Any) -> None:
    """재고 원장 쓰기를 하나의 전역 잠금으로 직렬화한다.

    ```text
    재고 원장 쓰기는 트랜잭션 수명의 advisory lock 하나로 의도적으로 직렬화한다.

    한 바깥 트랜잭션이 여러 재고 이동을 기록할 때 생기는
    자원 교차 교착(advisory lock ↔ row lock)을 이렇게 없앤다.

    MVP 의 정확성 우선 결정이며, 필요해지면 나중에
    일괄 잠금 프로토콜로 최적화할 수 있다.
    ```

    `move_id` 별 잠금은 교착을 막지 못한다. "여러 Move 를 기록할 때는 `move_id` 순서를
    고정하면 안전하다" 는 틀린 말이다.

    ```text
    T1  record(MOVE-1, LOT-A)   → adv(MOVE-1) 획득 · LOT-A row lock 획득
    T2  record(MOVE-2, LOT-A)   → adv(MOVE-2) 획득 · LOT-A 를 기다린다
    T1  record(MOVE-2, LOT-A)   → adv(MOVE-2) 를 기다린다
        ⇒ T1 은 T2 의 advisory 를, T2 는 T1 의 row lock 을 기다린다 — 교착
    ```

    정렬로 못 푸는 이유: T2 는 `MOVE-1` 을 요청한 적이 없다. 두 트랜잭션이 요청하는
    잠금 집합 자체가 달라서 전순서를 매길 수 없다. 두 잠금 모두 트랜잭션이 끝날 때까지
    안 풀리는 것이 이 교착의 뿌리다.

    ⇒ 잠금을 하나로 합치면 전순서가 저절로 성립한다. 기다리는 쪽은 아직 아무
      자원도 안 쥐고 있어 순환이 생길 자리가 없다.

    같은 트랜잭션 안에서는 재진입한다. 같은 세션이 같은 키를 몇 번 잡아도 자기를 막지
    않으므로, 한 트랜잭션이 `record_inventory_move` 를 여러 번 불러도 두 번째 호출이
    스스로 멈추지 않는다(실측 확인).

    transaction-level(`_xact_`) 이다. 바깥 트랜잭션의 커밋/롤백과 함께 자동으로 풀린다 —
    이 모듈은 unlock 을 부르지 않고, 부를 수도 없어야 한다. session-level 을 쓰면 풀어 줄
    주인이 없어 커넥션에 잠금이 눌어붙는다.

    커넥션을 새로 열지도, 커밋하지도 않는다. 호출자가 준 연결로 건다.

    제약: 대가는 쓰기 동시성이다. 서로 다른 Lot 에 대한 재고 이동도 한 줄로 선다.
    MVP 에서 이 거래는 받아들이기로 한 것이다 — 원장이 어긋나는 것보다 느린 것이 낫다.
    """
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL("SELECT pg_advisory_xact_lock(%s, %s)"),
            (LEDGER_LOCK_CLASSID, LEDGER_LOCK_OBJID),
        )


#: 도착 쓰기를 하나의 전역 잠금으로 직렬화한다. `lock_ledger_writes` 와 같은 판단이고,
#: 같은 `classid` 에 다른 `objid` 를 쓴다(좌표 전체는 모듈 머리말의 표).
#:
#: 입고별 키를 쓰지 않는다. 한 트랜잭션이 여러 입고를 처리하면 두 트랜잭션이
#: 요청하는 잠금 집합 자체가 달라 전순서를 매길 수 없고, 그것이 원장 건별 잠금의
#: 교착의 뿌리다(`lock_ledger_writes` docstring 에 그 시나리오가 있다).
ARRIVAL_LOCK_CLASSID = 20260905
ARRIVAL_LOCK_OBJID = 2


def lock_arrival_writes(conn: Any) -> None:
    """도착 Receipt 쓰기를 하나의 전역 잠금으로 직렬화한다.

    이 잠금이 read-before-write 의 틈을 닫는다.

    ```text
    잠금 없이   T1 조회 0건 · T2 조회 0건 → 둘 다 INSERT 시도
    잠금 있으면 T2 는 T1 의 커밋/롤백까지 기다렸다가 다시 조회한다
    ```

    transaction-level(`_xact_`) 이다. 바깥 트랜잭션의 커밋/롤백과 함께 자동으로 풀린다 —
    이 모듈은 unlock 을 부르지 않고, 부를 수도 없어야 한다. session-level 을 쓰면 풀어 줄
    주인이 없어 커넥션에 잠금이 눌어붙는다.

    같은 트랜잭션 안에서는 재진입한다. 한 트랜잭션이 여러 입고를 처리해도 두 번째
    호출이 스스로 멈추지 않는다.

    잠금 순서(원장 쓰기와 함께 지켜야 하는 계약):

    ```text
    ① 도착 전역 (20260905, 2)   ← 이 잠금. 가장 먼저
    ② fixture 행 FOR UPDATE      도착 경로 직렬화 · status 읽기
    ③ 원장 전역 (20260905, 1)    record_inventory_move 안에서
    ④ Lot 행 FOR UPDATE          〃
    ```

       원장 전역을 fixture 행보다 먼저 잡는 경로를 만들면 안 된다. 그 규칙이 이
       전순서를 성립시킨다.

    커넥션을 새로 열지도, 커밋하지도 않는다. 호출자가 준 연결로 건다.

    제약: 대가는 도착 처리의 직렬화다. 서로 다른 입고도 한 줄로 선다 — MVP 에서
    받아들인 거래이고, 원장 쓰기도 같은 거래를 받아들였다.
    """
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL("SELECT pg_advisory_xact_lock(%s, %s)"),
            (ARRIVAL_LOCK_CLASSID, ARRIVAL_LOCK_OBJID),
        )


#: 출고 쓰기 전역 잠금. `(…,1)` 원장 · `(…,2)` 도착과 겹치지 않는 빈 키다 (실측).
OUTBOUND_LOCK_CLASSID = 20260905
OUTBOUND_LOCK_OBJID = 3


def lock_outbound_writes(conn: Any) -> None:
    """출고 쓰기를 하나의 전역 잠금으로 직렬화한다.

    이 잠금이 가용량 경합을 닫는다.

    ```text
    잠금 없이   T1 available 100 · T2 available 100 → 각자 80 예약 → 160 이 나간다
    잠금 있으면 T2 는 T1 이 끝난 뒤 다시 세고 20 만 남은 것을 본다
    ```

    건별 잠금을 쓰지 않는다 — `lock_ledger_writes` 가 적어 둔 교착이 그대로 재현된다.
    한 트랜잭션이 여러 Lot 을 다루면 요청 잠금 집합이 달라져 전순서를 매길 수 없다.

    transaction-level 이라 호출자의 커밋/롤백과 함께 풀린다. unlock 을 부르지 않는다.
    """
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL("SELECT pg_advisory_xact_lock(%s, %s)"),
            (OUTBOUND_LOCK_CLASSID, OUTBOUND_LOCK_OBJID),
        )


#: 배치 쓰기 전역 잠금. `(…,1)` 원장 · `(…,2)` 도착 · `(…,3)` 출고와 겹치지 않는다.
WAREHOUSE_LOCK_CLASSID = 20260905
WAREHOUSE_LOCK_OBJID = 4


def lock_warehouse_writes(conn: Any) -> None:
    """Pallet 배치 쓰기를 하나의 전역 잠금으로 직렬화한다.

    이 잠금이 자리 경합을 닫는다.

    ```text
    잠금 없이   T1·T2 가 같은 빈 자리를 보고 각자 Pallet 을 앉힌다
                → uq_pallets_location 이 뒤늦게 터진다 (UniqueViolation 을 흐름으로 쓰지 않는다)
    잠금 있으면 T2 는 T1 이 끝난 뒤 다시 세고 그 자리가 찬 것을 본다
    ```

    transaction-level 이라 호출자의 커밋/롤백과 함께 풀린다. unlock 을 부르지 않는다.
    """
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL("SELECT pg_advisory_xact_lock(%s, %s)"),
            (WAREHOUSE_LOCK_CLASSID, WAREHOUSE_LOCK_OBJID),
        )
