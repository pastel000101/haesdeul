"""
inbound.py — 도착 예정이 실제로 들어오는 자리. 하루의 세 걸음 중 둘째.

이 진입점이 있는 이유: 입고 실행이 없으면 도착 예정분이 영원히 in_transit 에 남는다.
실측(2026-09-06): `INB-H1-THRU-20260105-BAECHU-1-1` 은 `expected_arrival_date =
2026-01-07` 인데 2026-02-06 까지 32행 내내 in_transit 이었다. 그동안 창고 점유를 계속
먹는다 — `cap_by_date` 가 안 온 물건을 30일 내내 예정으로 잡는다. 관통을 길게 돌릴수록
창고가 가짜로 찬다.

`day_open` 에 넣지 않는 이유는 둘이다.

```text
① day_open 의 계약은 "그날 상태 행을 보장한다" 이고 아무것도 실행하지 않는다
   Arrival 이하는 사건이다 — 로트를 만들고 재고를 늘린다

② day_open 은 마스터가 모든 파트에 대해 부르는 공통 진입점이다
   거기에 물류 전용 실행을 넣으면 재무가 열릴 때도 입고가 돈다
```

그리고 "하루를 열었다" 와 "물건을 받았다" 가 한 함수가 되면 실패했을 때 무엇이 안
됐는지가 뭉개진다.

하루의 세 걸음이 각자 멱등하고 각자 실패한다.

```text
① open_day(as_of)          상태 행을 보장한다        ← 먼저 (적을 자리가 있어야 한다)
② receive_arrivals(as_of)  도착분을 실제로 받는다     ← 여기
③ run_procurement(as_of)   그 위에서 판단한다
```

한 함수로 묶지 않는다. 묶으면 "왜 실패했나" 가 뭉개지고, 셋이 각자 멱등할 때
재시도가 안전하다.

날마다 돈다. 실행일이 아니다. 창고는 토요일에도 물건을 받는다 — `is_open` 은 시장이
서는가이지 창고가 여는가가 아니다. `open_day` 와 같은 결이다(`#240` — "실행일은
평일만, 경과일수는 달력일").

파트가 물류 하나다. 입고가 재고를 늘리면 재무 `inventory_book_value_krw` 도 움직여야 할
수 있는데, 그 판단은 재무 몫이라 여기서 정하지 않는다. 등록소(`registry/inbound.py`)를
파트별로 두는 이유가 그것이다 — 재무가 필요하다고 하면 한 줄로 붙는다.

제약: 입고된 뒤에는 취소가 안 된다. 물건이 창고에 있으면 취소가 아니라 반품이다
(재무의 "`SETTLED` 는 fail-closed" 와 같은 성격). 그 방어는 파트가 세우고, 이 모듈은
파트가 거절하면 통째로 롤백한다.
"""

from __future__ import annotations

from datetime import date

from app.contracts.parts import InboundPartOut
from app.core import db as core_db
from app.master.registry.inbound import PARTS, missing, registered
from app.master.registry.sim_run_binding import bind_sim_run
from app.master.schemas.inbound import InboundOut
from app.master.service.day_gate import check_day_gate

# ── 경계 ────────────────────────────────────────────────────────────────


def receive_arrivals(
    as_of: date, *, borrow: core_db.Borrow | None = None, sim_run_id: str
) -> InboundOut:
    """`as_of` 에 도착 예정인 것을 한 트랜잭션으로 받는다.

    `open_day` 다음이다. 상태 행이 있어야 입고를 적을 자리가 있다. 다만 함수는 따로다
    — 묶으면 실패 원인이 뭉개진다.

    실패 처리: 예외를 밖으로 내지 않는다. `apply_approval` · `undo_approval` 과 같다 —
    입고 실패가 판단을 멈추면 그날 하루가 통째로 서고, 그건 입고 하나보다 크다.

    예외 전파 여부와 후속 진행 여부는 다른 물음이다. 예외를 올리지 않는 것이 이 함수의
    계약이지만, 그렇다고 판단을 계속한다는 뜻은 아니다. 입고가 `FAILED` · `BLOCKED`
    인데 매입 판단을 계속하면 현재고와 capacity 가 실제보다 적게 반영된 상태로
    판단한다 — 받았어야 할 물건이 장부에 없는 채로 "창고가 비었으니 더 사자" 가
    나온다. 그래서 부르는 쪽이 정한다. 이 함수는 상태를 값으로 돌려주고,
    `run_procurement` 진행 여부는 그것을 본 orchestration 의 결정이다.

    달력일이다. 창고는 토요일에도 받는다. 실행일 달력을 쓰지 않는다.

    개장 Gate 를 먼저 본다. 순서를 문장으로만 적어 두면 지키는 책임이 부르는 쪽에
    통째로 있고, 안 지킨 날 `NOTHING_DUE` 가 나가 "오늘은 올 게 없었다" 로 읽힌다.

      ```text
      Gate BLOCKED   →  NOT_OPENED     받을 것이 있는지조차 안 묻는다
      Gate PASS      →  평소대로
      ```

    `check_day_gate` 는 열지 않고 묻기만 한다. 여기서 `open_day` 를 부르면 입고가
    개장의 부작용이 되고, 개장 API(`app/api/master/days.py`)가 적어 둔 "명시적
    호출이다. 실행의 부작용이 아니다" 를 입고가 어긴다. 미등록은 PASS 다(`day_gate`
    계약). 정본 표가 없는 환경에서 이 Gate 가 입고를 막지 않는다 — 없는 것과 안 열린
    것은 다르다.

    :param sim_run_id: 어느 실행의 장부인가(`#531`). 기본값이 없다. 번인 상수로 메우면
                    축을 안 준 호출이 조용히 번인 장부에 쓴다. 걷기는
                    `run_scheduled_day` 가, 라우터는 요청이 준 축을 싣는다.
    """
    gate = check_day_gate(as_of, borrow=borrow, sim_run_id=sim_run_id)
    if gate.gate == "BLOCKED":
        return InboundOut(
            as_of=as_of,
            status="NOT_OPENED",
            reason=gate.reason,
            # 해석하지 않고 옮긴다. 무엇을 해야 하는지는 개장이 아는 사실이고,
            # 입고가 다시 판정하면 같은 사실의 주인이 둘이 된다.
            next_action=gate.next_action,
        )

    absent = missing()
    if absent:
        # 미등록은 오류가 아니다. 그 파트가 입고를 실행하지 않는다는 뜻이고,
        # "오늘 받을 것이 없다" 와 다른 사실이다.
        return InboundOut(
            as_of=as_of,
            status="NOTHING_DUE",
            reason=f"입고 실행 미등록: {', '.join(absent)}",
            missing=list(absent),
        )

    open_connection = core_db.connection if borrow is None else borrow
    with open_connection() as conn:
        try:
            # 등록소가 든 축이 아니라 이번 호출의 축으로 묶는다(`#531`).
            # 물류 `LogisticsInboundExecution` 은 그 축의 로트·이동을 쓴다 —
            # 등록소가 프로세스 시작 때 든 상수로 쓰면 매입 원장만 새 실행에
            # 앉고 이쪽은 번인에 남는다.
            #
            # `try` 안이다. 축이 비면 `bind_sim_run` 이 막는데, 그 실패도 예외로
            # 올라가지 않고 아래 `except` 가 `FAILED` + 사유로 옮긴다 — 입고가
            # 그날을 통째로 세우면 안 된다는 이 함수의 계약 그대로다.
            adapters = {
                part: bind_sim_run(impl, sim_run_id) for part, impl in registered().items()
            }
            results = [adapters[part].receive(conn, as_of=as_of) for part in PARTS]
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - 입고 실패가 그날을 통째로 세우면 안 된다.
            conn.rollback()
            return InboundOut(as_of=as_of, status="FAILED", reason=f"입고 실행 실패: {exc}")

    return _aggregate(as_of, results)


def _aggregate(as_of: date, parts: list[InboundPartOut]) -> InboundOut:
    """파트 결과를 전체 어휘로 취합한다.

    ```text
    BLOCKED 가 하나라도   → BLOCKED     받을 게 있었는데 못 받았다
    RECEIVED 가 하나라도  → RECEIVED
    전부 NOTHING_DUE      → NOTHING_DUE
    ```

    순서가 계약이다. `BLOCKED` 를 먼저 보는 이유는 그것이 사람이 봐야 하는 사실이기
    때문이다 — `RECEIVED` 뒤로 밀면 "오늘 받았다" 로 지나간다.
    """
    blocked = [part for part in parts if part.status == "BLOCKED"]
    if blocked:
        return InboundOut(
            as_of=as_of,
            status="BLOCKED",
            reason=f"받을 것이 있는데 막혔다: {', '.join(part.part for part in blocked)}",
            parts=parts,
        )
    if any(part.status == "RECEIVED" for part in parts):
        return InboundOut(as_of=as_of, status="RECEIVED", parts=parts)
    return InboundOut(as_of=as_of, status="NOTHING_DUE", parts=parts)
