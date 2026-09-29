"""
cancellation.py — **취소된 승인의 입고 예정을 걷는다.**

```text
승인 취소  →  그 승인의 inbound_id 들을 조립          (inbound_ids_of)
          →  Receipt 가 하나라도 있으면 아무것도 안 걷는다 (assert_cancellable)
          →  inbound_schedules.cancelled_as_of 에 목표 상태일을 적는다
          →  마스터 취소 근거를 cancel_source_ref 에 함께 적는다
             (생성 근거 source_ref 는 그대로 둔다)
```

★ **마스터 승인 취소를 물류 입고 일정 취소 경로에 잇는 자리다** (`day_open.py` 와
  같은 모양).

🔴 **정본은 `inbound_schedules` 한 표다 (W3-3).** 종전에는 그날 fixture 행의
  `in_transit` · `confirmed_inbound` JSON 목록에서 항목을 뺐지만, Reader 가 더 이상
  그 칸을 읽지 않는다.

🔴 **과거 행을 안 고친다.**

```text
승인 01-05  →  01-06 부터 서 있다
취소 01-07  →  cancelled_as_of = 01-08 (목표 상태일)

01-06 · 01-07 은 그대로 둔다 — **그때는 실제로 오는 중이었다.**
```

  ★ 재무 역분개와 **같은 규율**이다 (`#302` — *"과거 state rewrite 금지"*).

🔴 **`FOR UPDATE` 로 그 행 하나를 잠그고 읽고-고치고-쓴다** (`cancel_schedule`) —
  읽기와 쓰기 사이의 틈을 닫는 것은 행 잠금뿐이다.

⚠️ **입고된 뒤에는 이 함수로 못 물린다.** 물건이 창고에 있으면 취소가 아니라
  반품·폐기·실사이고, `assert_cancellable` 이 Receipt 를 먼저 보고 거절한다.

★ 2026-09-30 재구성 BL-015: `logistics/cancellation.py` 에서 옮겼다. 입고 ID
  규칙(`inbound_ids_of`)은 `domain/transition.py`, 등록소 표면은
  `adapter.LogisticsCancellationAdapter`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from app.logistics.service.inbound_schedules import assert_cancellable, cancel_schedule


def withdraw_inventory(
    conn: Any,
    *,
    sim_run_id: str,
    as_of: date,
    inbound_ids: Sequence[str],
    source_ref: str,
) -> int:
    """이 입고 건들을 **`as_of` 그날부터** 취소한다.

    🔴 **정본은 `inbound_schedules.cancelled_as_of` 하나다 (W3-3).** 종전에는 그날
       fixture 행의 두 JSON 칸에서도 항목을 빼야 했는데, Reader 가 더 이상 그 칸을
       읽지 않으므로 걷을 이유가 없어졌다.

    ★ 받은 `source_ref` 는 `cancel_source_ref` 칸에 적는다 — 일정의
      `source_ref`(생성 근거)는 덮지 않는다.

    ```text
    as_of <  cancelled_as_of   그날 이 일정은 여전히 존재한다
    as_of >= cancelled_as_of   그날부터 취소다
    ```

       ⚠️ 받는 `as_of` 는 이미 `cancelled_on + 1`(목표 상태일)이다
          (`LogisticsCancellationAdapter.cancel`). 취소일 자체를 적으면 **이미
          지나간 하루의 사실이 바뀐다.**

    🔴 **commit 하지 않는다.** 커밋은 재무 취소·매입 원장과 함께 마스터가 한 번 한다.

    🔴 **자기 커넥션을 새로 열지 않는다.** `persist_inventory` 와 같은 이유다.

    🔴 **도착 Receipt 가 있으면 거절한다.** 물건이 도착했으면 취소가 아니라
       반품·폐기·실사이고, 그 판단을 물류가 대신 내리지 않는다
       (`service/reconciliation` 이 긋는 그 선과 같다).

      ⚠️ **판정을 쓰기보다 먼저 한다.** 하나씩 취소하다 중간에 막히면 앞의 것은 이미
         닫힌 뒤다 — 같은 트랜잭션이라 롤백은 되지만, 판정이 쓰기와 섞이면
         *"무엇이 왜 막혔나"* 가 흐려진다.

    ★ **없는 것을 걷어도 오류가 아니다** — 이미 취소된 뒤의 재시도가 그렇다. 그때
      돌려주는 값이 `0` 이라 마스터가 *"이번에 실제로 걷은 것"* 을 말할 수 있다
      (재무 `#302` 의 *"retry no-op"* 과 같은 모양).

    :returns: 이번 호출로 **실제로 취소된 일정 수.**
    :raises ScheduleReceiptExists: 도착 Receipt 가 있는 입고를 취소하려 할 때.
    :raises ScheduleCancelConflict: 이미 **다른 날짜로** 취소된 일정일 때.
    """
    drop = sorted({i for i in inbound_ids if i})
    if not drop:
        # ★ 회차 일정이 없던 약정도 승인은 살아 있다 — 걷을 입고가 **없다**는 것은
        #   정상 상태다 (마스터 `cancel_purchases` 와 같은 태도).
        return 0

    # ── 판정이 먼저다. 하나라도 도착했으면 아무것도 안 걷는다 ──────────
    assert_cancellable(conn, sim_run_id=sim_run_id, inbound_ids=drop)

    return sum(
        1
        for inbound_id in drop
        if cancel_schedule(
            conn,
            sim_run_id=sim_run_id,
            inbound_id=inbound_id,
            cancelled_as_of=as_of,
            cancel_source_ref=source_ref,
        )
    )
