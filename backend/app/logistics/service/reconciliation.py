"""사람이 확인한 고착 입고 일정을 명시적으로 닫는다. 고착 일정 정리의 순서다.

```text
운영자가 확인한 orphan 일정
   → 도착 전역 잠금 → 입고 계보 확인
   → inbound_schedules.cancel_schedule 로 그날부터 닫는다 (cancelled_as_of)
```

이 파일은 도착 로직의 버그를 고치는 것이 아니다. 발주 참조가 없는 일정이
`ARRIVAL_PURCHASE_REFERENCE_MISSING` 으로 blocked 에 남는 것은 정상 동작이다
(`domain/arrival.py` 의 그 판정 — "물류가 값을 지어내 풀 일이 아니다"). 여기 있는 것은
그 판정을 우회하는 길이 아니라, 사람이 따로 확인한 뒤 명시적으로 거두는 길이다.

---

## 안 하는 것 다섯 — 이 파일의 존재 이유가 여기 있다

```text
① ETA + N일 지났다고 자동 삭제        오래된 것과 잘못된 것은 다른 사실이다
② inbound_id 를 파싱해 purchase_id 조립  발주 ID 의 주인은 물류가 아니다
③ 수량·날짜·품목이 같으면 같은 입고    실데이터에 3,587kg Receipt 가 여럿 있고
                                       inbound_id 는 서로 다르다
④ 한쪽 목록만 삭제                    B-1 위반을 덮는 것이 된다
⑤ Receipt 가 있는데 일정만 삭제        그것은 orphan 이 아니라 무결성 문제다
```

다섯 모두 자동 판단을 하지 않는다는 한 줄이다. 이 함수는 "무엇을 걷을지" 를
스스로 고르지 않는다 — `inbound_id` 를 사람이 지목하고, `reason` · `source_ref` 로
근거를 함께 낸다. 근거 없는 복구는 근거 없는 삭제와 같은 것이다.

---

## 정상 입고 경로를 대체하지 않는다

```text
검수 끝난 입고   inbound_stock.materialize_inspected_inbound   Receipt·검수·Lot·원장 IN
고착 orphan     여기                                          일정만, 사람이 지목해서
```

`Receipt` 가 하나라도 있으면 이 함수는 거부한다. 그 건은 도착 처리가 이미
시작됐다는 뜻이라 `materialize_inspected_inbound` 의 몫이거나, 아니면 별도
무결성 문제다 — 어느 쪽이든 일정만 걷어서 될 일이 아니다.

---

## 규율은 새로 만들지 않고 가져다 쓴다

```text
멱등 · 다른 날짜 충돌 판정
    → inbound_schedules.cancel_schedule 로 그날부터 닫는다
도착 전역 advisory 잠금
    → repository/locks.lock_arrival_writes
usage_scope
    → schemas/vocabulary.USAGE_SCOPE
```

취소 규칙을 여기에 다시 적지 않는다. 멱등·다른 날짜 충돌 판정은
`inbound_schedules.cancel_schedule` 하나가 소유한다 — 두 곳이 각자 적으면 한쪽만
고쳐지는 날이 온다.

`cancellation.withdraw_inventory` 를 부르지 않는다. 그쪽은 마스터 승인 취소의 입구이고,
두 경로가 공유하는 취소 규칙은 `inbound_schedules.cancel_schedule` 에 있다 — 둘 다 그
함수를 부르고, 코드를 두 벌로 만들지 않는다.

---

## 잠금 순서 — `materialize_inspected_inbound` 과 같은 순서

```text
① 도착 전역 advisory (20260905, 2)   repository/locks.lock_arrival_writes
② 입고 계보 조회 (SELECT)            ①이 잡혀 있어 그 사이 Receipt 가 못 생긴다
③ 일정 행 FOR UPDATE                 cancel_schedule 안에서
④ 일정 UPDATE                        〃 같은 잠금 아래
⑤ 커밋은 호출자가 한 번               이 파일은 commit 도 rollback 도 하지 않는다
```

②를 ① 앞에 두면 안 된다. 그러면 계보를 읽은 뒤 걷기 전에 도착 처리가 끼어들어
"Receipt 가 없다" 로 읽은 사실이 걷는 순간 거짓이 된다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.logistics.domain.reconciliation import reconciliation_text
from app.logistics.readmodel.inbound_schedules import load_schedule_views
from app.logistics.repository.locks import lock_arrival_writes
from app.logistics.repository.reconciliation import select_materialized_lineage
from app.logistics.schemas.reconciliation import (
    InboundReconciliationResult,
    ScheduleAlreadyMaterialized,
)
from app.logistics.schemas.vocabulary import USAGE_SCOPE
from app.logistics.service.inbound_schedules import cancel_schedule


def reconcile_orphan_inbound_schedule(
    conn: Any,
    *,
    sim_run_id: str,
    as_of: date,
    inbound_id: str,
    reason: str,
    source_ref: str,
    usage_scope: str = USAGE_SCOPE,
) -> InboundReconciliationResult:
    """사람이 확인한 고착 입고 일정 한 건을 그날부터 닫는다.

    ```text
    ① 도착 전역 잠금
    ② inbound_id 의 입고 계보 조회   Receipt 0 / 1 / 2+ 를 가른다
    ③ 계보가 있으면 거부              ScheduleAlreadyMaterialized · InboundLineageAmbiguous
    ④ 그 일정 한 건을 신규 표에서 읽는다 (돌려줄 사실 확보)
    ⑤ inbound_schedules 를 그날부터 닫는다   cancelled_as_of
    ```

    정리의 정본은 `cancelled_as_of` 한 칸이다. 이 함수가 하는 일이 «그 일정을
    그날부터 없앤다» 이므로 그 칸이 그 사실의 자리다.

    일정 행을 지우지 않는다. 지우면 "그날 무엇이 떠 있었나" 를 되짚을 자리가
    없어진다 — 사람이 치웠다는 사실도 함께 사라진다.

    나이로 지우지 않는다. `expected_arrival_date` 가 얼마나 지났는지, 발주 참조가
    비었는지, `ARRIVAL_PURCHASE_REFERENCE_MISSING` 인지를 조건으로 쓰지 않는다 —
    참조 전달이 늦은 정상 입고와 구별되지 않기 때문이다. 이 함수가 보는 것은 사람이
    지목한 `inbound_id` 하나의 정합성뿐이다.

    `inbound_id` 를 이 함수가 고르지 않는다. 무엇이 잘못된 일정인지는 사람이
    판단하고, 이 함수는 그 판단을 정확히 한 건만 실행한다. 그래서 조건 검색도
    일괄 정리도 없다.

    멱등이다. 이미 걷힌 건을 같은 요청으로 다시 불러도 예외가 아니라
    `applied=False · removed=0` 이다.

      다른 날짜로 이미 닫힌 건은 멱등이 아니다. "언제 정리했나" 가 둘이 될 수
      없어 `ScheduleCancelConflict` 로 멈춘다 — no-op 으로 접으면 그 갈림을 덮는다.

    커밋도 롤백도 하지 않는다. advisory 잠금도 행 잠금도 트랜잭션 수명이라
    호출자의 커밋/롤백과 함께 풀린다 (`materialize_inspected_inbound` 과 같은 규율).

    :param conn: 호출자가 소유한 커넥션. 이 함수는 수명을 관리하지 않는다.
    :param sim_run_id: 어느 실행의 장부인가. 비울 수 없다.
    :param as_of: 닫을 날짜. 그날부터 닫는다 — 그 전 날들은 그때 실제로 오는 중이었으므로
        고치지 않는다 (`cancellation.py` 와 같은 규율).
    :param inbound_id: 사람이 지목한 입고 건. 추측하지 않는다.
    :param reason: 왜 걷는가. 빈 문자열을 안 받는다.
    :param source_ref: 그 판단의 근거 참조. 빈 문자열을 안 받는다.
    :raises InvalidReconciliationRequest: 축이나 근거가 비었을 때. DML 전에 막는다.
    :raises ScheduleAlreadyMaterialized: 그 `inbound_id` 에 Receipt·Lot·IN 이 있을 때.
    :raises InboundLineageAmbiguous: 같은 `inbound_id` 에 Receipt 가 둘 이상일 때.
    :raises ScheduleCancelConflict: 그 일정이 이미 다른 날짜로 닫혀 있을 때.
    """
    reconciliation_text(sim_run_id, 칸="sim_run_id")
    reconciliation_text(inbound_id, 칸="inbound_id")
    reconciliation_text(usage_scope, 칸="usage_scope")
    reconciliation_text(reason, 칸="reason")
    reconciliation_text(source_ref, 칸="source_ref")

    # ── ① 도착 전역 잠금이 먼저다 ─────────────────────────────────────
    #    계보 조회보다 앞이어야 한다. 뒤에 두면 "Receipt 가 없다" 로 읽은 사실이
    #    걷는 순간 거짓이 될 수 있다.
    lock_arrival_writes(conn)

    # ── ② · ③ 계보가 있으면 여기서 멈춘다 ─────────────────────────────
    계보 = select_materialized_lineage(conn, sim_run_id=sim_run_id, inbound_id=inbound_id)
    if 계보:
        raise ScheduleAlreadyMaterialized(
            f"이미 입고 계보가 붙은 건이라 일정만 걷을 수 없다 (inbound_id={inbound_id!r},"
            f" sim_run_id={sim_run_id!r}): {계보!r}."
            " orphan 일정과 입고된 사실은 다른 문제다 — 일정을 지우면 그 재고의"
            " 출처를 되짚을 자리가 없어진다."
        )

    # ── ④ 돌려줄 사실을 신규 표에서 쥔다 ──────────────────────────────
    #    아직 살아 있는 일정만 나온다 (`load_schedule_views` 가 취소분을 뺀다) —
    #    그래서 이미 닫힌 건에 다시 부르면 아래가 자연히 no-op 이 된다.
    #    `as_of` 시점 목록이라 그날 이후에 선 일정은 안 보인다. 정리는 그날부터
    #    닫는 일이므로 그 눈이 맞다.
    사실 = next(
        (
            보기
            for 보기 in load_schedule_views(conn, sim_run_id=sim_run_id, as_of=as_of)
            if 보기.inbound_id == inbound_id
        ),
        None,
    )

    # ── ⑤ 그날부터 닫는다 ─────────────────────────────────────────────
    #    이미 같은 날짜로 닫혀 있으면 `cancel_schedule` 이 `False`(멱등)이고,
    #    다른 날짜로 닫혀 있으면 `ScheduleCancelConflict` 로 멈춘다.
    applied = cancel_schedule(
        conn, sim_run_id=sim_run_id, inbound_id=inbound_id, cancelled_as_of=as_of
    )

    return InboundReconciliationResult(
        applied=applied,
        inbound_id=inbound_id,
        sim_run_id=sim_run_id,
        as_of=as_of,
        usage_scope=usage_scope,
        # 이번 호출로 닫힌 일정 수 — 지목한 한 건이 닫혔으면 1, 이미 닫혀 있었으면 0.
        removed=1 if applied else 0,
        reason=reason,
        source_ref=source_ref,
        item=None if 사실 is None else 사실.item_name,
        quantity_kg=None if 사실 is None else 사실.quantity_kg,
        expected_arrival_date=None if 사실 is None else 사실.expected_arrival_date,
    )
