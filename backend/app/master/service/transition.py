"""
transition.py — 승인 → 상태전이의 **트랜잭션 경계** (C 형태 ⑦)

사람이 매입안을 승인하면 `commitment.py` 가 확정 입고 약정을 만든다. 그 약정이
재무 현금 장부와 물류 재고 장부를 **실제로 바꾸는** 자리가 여기다.

```text
승인 → ApprovedCommitment → [ 재무 write · 물류 write ] → 한 커밋
                             └────────── 이 파일이 감싼다 ──────────┘
```

★ **마스터는 무슨 값을 어느 칸에 쓸지 모른다.** 그것은 재무·물류가 소유한다.
  각 파트가 `build`(순수 계산)와 `persist(conn, ...)`(주어진 커넥션으로 write)를
  내고, 마스터는 **언제 부를지와 언제 커밋할지**만 정한다.

  ```text
  무슨 값을 어느 칸에 어떤 SQL 로   재무 · 물류 소유
  언제 · 한 트랜잭션으로 커밋        마스터 소유 ← 이 파일
  ```

🔴 **이 모듈에 SQL 이 있으면 그 분담이 무너진다.** 여기에 `INSERT` 를 한 줄이라도
   적는 순간 마스터가 `finance_states` 의 칸 이름을 알게 되고, 재무가 칸을 바꿀 때
   조용히 어긋난다. `test_transition_boundary.py` 가 원문을 읽어 그것을 막는다.

★ **두 파트를 한 커밋으로 묶는 이유.** 재무만 커밋되고 물류가 터지면, 현금은
  나갔는데 입고 예정은 없는 장부가 된다 — 그 상태를 **아무도 틀렸다고 말해 주지
  않는다.** 둘 다 되거나 둘 다 안 되어야 한다.

★ **어댑터 미등록은 오류가 아니라 상태다** (`wiring.py` · `service.py` 와 같은 태도).
  재무·물류 구현이 아직 없는 지금 이 경로가 500 을 내면, 승인 자체가 막힌다.
  그건 *"그 부서가 오늘 돌지 않는다"* 이지 승인이 실패한 것이 아니다.

🔴 **`with conn:` 을 쓰지 않는다.** psycopg3 의 커넥션 컨텍스트 매니저는 블록이
   정상 종료하면 **자동으로 commit** 한다. 그러면 "커밋은 마스터가 한 번만 한다"는
   이 파일의 유일한 일이 문법에 숨어 버리고, 변이 검사(커밋 지우기)도 안 걸린다.
   commit · rollback 을 눈에 보이게 적는다. 연결은 공통 풀에서 `with borrow() as conn:` 으로
   빌리고 블록 끝에 돌려준다 — **돌려줄 때 commit 하지 않는다** (2026-09-29 풀 전환 ·
   `app/core/db.py`). 종전 `close()` 자리다.

★ 2026-09-30 재구성 BL-018: `master/transition.py` 에서 옮겼다. 역할이 다른 부분은 갈랐다 —
  `domain/purchase_ids.py`; `domain/transition.py`; `registry/transition.py`;
  `schemas/transition.py`. 무엇이 어디로 갔는지는 설계서 대응표 `master/` 절.
"""

from __future__ import annotations

from app.contracts.commitment import ApprovedCommitment
from app.core import db as core_db
from app.master.domain.ledger import (
    BLOCK_NO_ARRIVAL,
    LedgerBlock,
    build_purchase_rows,
    ledger_block,
)
from app.master.domain.purchase_ids import purchase_id_for
from app.master.domain.transition import arrival_block_reason, target_state_date_of
from app.master.registry.sim_run_binding import bind_sim_run
from app.master.registry.transition import PARTS, missing, registered
from app.master.repository.ledger import persist_purchases
from app.master.schemas.transition import TransitionOut

# ── 원장을 쓸 수 있는 상태인가 ──────────────────────────────────────────


def _ledger_blocked(commitment: ApprovedCommitment) -> LedgerBlock | None:
    """매입 원장을 쓸 수 없으면 그 **갈래와 사유**. 쓸 수 있으면 `None`.

    ★ **`FAILED` 가 아니라 `NOT_APPLIED` 로 가는 자리다.** 둘 다 아직 못 쓴 상태지만,
      여기 걸리는 것은 *"바꾸려다 실패했다"* 가 아니라 *"쓸 값이 아직 없다"* 다.

    🔴 **판정도 문장도 여기서 짓지 않는다.** 주인은 `ledger.ledger_block_reason` 이고
       `build_purchase_rows` 가 최후 방어로 같은 함수를 다시 부른다. 여기 규칙을 한 줄
       복사해 두면 원장이 여는 조건과 전이가 여는 조건이 갈리는 날이 온다.

    ★ **회차가 둘 이상이어도 회차 금액이 다 실려 있으면 지나간다** (2026-09-08).
      전에는 무조건 막았고 그 시절 주석은 *"재무도 같은 이유로 무조건 막는다"* 고
      적었는데 **사실이 아니었다** — 재무 `_payment_legs`
      (`app/finance/domain/transition.py`)는 `len(legs) > 1` 일 때 금액이 비었으면만
      막는 **조건부**다. 이제 두 곳이 같은 조건으로 막는다.

    ★ 회차가 **하나도 없는** 경우는 여기서 가르지 않는다 — 그건 원장 이전에 재무가
      `commitment_arrival_schedule` 로 먼저 막는 상태이고, 그 사유를 여기서 다시
      쓰면 같은 사실이 두 문장으로 나간다.

    ⚠️ **갈래를 문장과 같이 받는다** (2026-09-16). 요약이 사유별로 세려면 문장이
      아니라 갈래가 필요한데, 갈래를 여기서 문장으로부터 되짚으면 **판정의 주인이
      둘**이 된다. 그래서 주인에게 둘을 한 번에 받는다.
    """
    return ledger_block(commitment)


# ── 트랜잭션 경계 ───────────────────────────────────────────────────────


def apply_approval(
    commitment: ApprovedCommitment,
    *,
    sim_run_id: str | None,
    borrow: core_db.Borrow | None = None,
) -> TransitionOut:
    """승인분을 재무·물류 장부에 **한 트랜잭션으로** 반영한다.

    순서가 이 함수의 전부다.

    ```text
    1. 미등록 확인    → 커넥션을 열지 않는다
    2. 회차·지급일 확인 → 쓸 수 없으면 NOT_APPLIED, 역시 커넥션을 열지 않는다
    2'. 도착분 확인    → 목표 상태일에 앞으로 올 도착분이 없으면 NOT_APPLIED
    3. build 세 번    → 커넥션 밖에서 (실패해도 DB 를 안 건드린다)
    4. persist 세 번  → 한 커넥션으로 · **매입 원장이 재무보다 먼저**
    5. commit 한 번   → 실패하면 rollback
    ```

    ★ **매입 원장이 재무보다 먼저인 이유는 FK 다.** `payables.purchase_id` 가
      `purchases` 를 참조한다 — 부모 행이 없으면 재무 write 가 FK 에서 터진다.

    🔴 **여기에 SQL 은 없다.** 무슨 값을 어느 칸에 쓸지는 `master/ledger.py` 가 알고,
       이 함수는 **언제 부를지**만 정한다 (`test_전이_모듈에_SQL_이_없다`).

    🔴 **예외를 밖으로 던지지 않는다.** 이 함수가 불릴 때 결정은 **이미 적재됐다.**
       전이 실패가 예외로 올라가면 라우터가 500 을 내고, 사람이 보기에는 승인이
       실패한 것이 된다 — 실제로는 승인은 남았고 장부만 안 바뀐 것이다.
       `decision.py` 가 *"약정 조립 실패가 결정을 지우면 안 된다"* 고 정한 것과
       같은 규율이고, 여기서는 그것이 한 단계 더 뒤에 걸린다.

    ★ **다만 삼키되 사유는 반드시 남긴다.** 조용히 `FAILED` 만 돌려주면 무엇이
      터졌는지 아무 데도 안 남는다.

    :param sim_run_id: 어느 실행의 장부에 반영하는가. 🔴 **기본값이 없다** — 이
                    함수는 축을 **나르기만** 하고 상수를 읽지 않는다
                    (`ledger.sim_run_id_for`). 축이 없으면 원장 계산이 터지고
                    `FAILED` 가 사유를 싣는다 — 조용히 번인에 앉지 않는다.
    :param borrow: 연결을 빌려 주는 함수(`with borrow() as conn:` 끝에 돌려준다). 안 주면
                    `app.core.db.connection`(공통 풀) — 재무·물류가 같은 DB(같은 `DB_*`)를
                    쓰므로 연결도 하나면 된다. commit · rollback 은 여기서 눈에 보이게 한다.
    """
    absent = missing()
    if absent:
        # ★ 여기서 돌아선다 — **커넥션을 열지 않는다.** 열고 나서 아무 일도 안 하면
        #   빈 트랜잭션이 매 승인마다 열렸다 닫힌다.
        return TransitionOut(
            status="NOT_APPLIED",
            reason=f"상태전이 미등록: {', '.join(absent)}",
            missing=list(absent),
        )

    blocked = _ledger_blocked(commitment)
    if blocked is not None:
        # ★ **`FAILED` 가 아니다.** 쓸 수 없다는 것은 우리가 아는 사실이지 실패가
        #   아니다. 그리고 여기서도 **커넥션을 열지 않는다.**
        #
        # 🔴 **갈래를 같이 싣는다** (2026-09-16). 문장만 실으면 세는 쪽이 약정마다
        #    다른 키를 보고, 승인은 났는데 원장에 한 행도 안 남은 건수가 요약에서
        #    안 보인다. **싣기만 한다 — 돌아서는 자리도 사유도 그대로다.**
        return TransitionOut(status="NOT_APPLIED", reason=blocked.reason, block_kind=blocked.kind)

    target_state_date = target_state_date_of(commitment)
    # 🔴 **`_ledger_blocked` 와 나란히 선다** — 트랜잭션 밖에서 막아야
    #    `purchases` 도 재무 행도 안 써진다. 리드타임 0 일 때 물류만 조용히 빠지던
    #    자리이고, 여기 걸리는 것은 오류가 아니라 **아무도 안 정한 상태**다.
    arrival_blocked = arrival_block_reason(commitment, target_state_date)
    if arrival_blocked:
        # ★ **갈래 이름은 `ledger` 것을 가져다 쓴다** — 문장은 여기가 짓지만 이름까지
        #   여기서 지으면 요약이 세는 갈래가 둘이 된다.
        return TransitionOut(
            status="NOT_APPLIED", reason=arrival_blocked, block_kind=BLOCK_NO_ARRIVAL
        )

    try:
        # 🔴 **등록소가 든 축이 아니라 이 승인의 축으로 묶는다** (`#531` 후속).
        #    전에는 물류 어댑터가 프로세스 시작 때 받은 상수를 들고 있어서, 매입
        #    원장이 새 실행에 앉는 날 **물류 장부만 번인에 남았다.**
        #
        # ★ **`try` 안이다.** 축이 비면 `bind_sim_run` 이 막는데, 그 실패는 예외로
        #   올라가지 않고 아래 `except` 가 `FAILED` + 사유로 옮긴다 —
        #   `build_purchase_rows` 가 같은 이유로 터지는 것과 한 자리에서 걸린다.
        transitions = registered()
        finance = bind_sim_run(transitions["finance"], sim_run_id)
        logistics = bind_sim_run(transitions["logistics"], sim_run_id)
        # 🔴 **커넥션 밖에서 계산한다.** 순수 계산이 터지는 것은 흔한 일인데
        #   (약정 모양이 예상과 다르다 등), 커넥션을 연 뒤에 터지면 열린 트랜잭션이
        #   남는다. 계산 실패는 DB 를 만나기 전에 끝나야 한다.
        # ★ `target_state_date` 는 위에서 이미 섰다 — 새 가드가 같은 날을 봐야 한다.
        # ★ 회차마다 `purchases` 한 행이므로 회차마다 ID 하나다. `arrival_schedule`
        #   이 비면 **빈 매핑**이고 그것은 예외가 아니다 — 회차 일정을 못 만든 약정도
        #   승인은 살아 있다 (`commitment.py` 의 `notes` 가 왜 못 만들었는지 적는다).
        purchase_ids = {
            leg.seq: purchase_id_for(commitment, leg.seq) for leg in commitment.arrival_schedule
        }
        # ★ 매입 원장도 **커넥션 밖에서** 계산한다 — 재무·물류와 같은 규율이다.
        #   `items` 조회만 커넥션이 필요하고 그것은 `persist_purchases` 안에 있다.
        # 🔴 **받은 축을 그대로 넘긴다.** 여기서 상수를 읽으면 이 함수가 축의
        #    주인이 되고, 부르는 쪽이 무엇을 지정하든 소용이 없어진다.
        ledger_rows = build_purchase_rows(
            commitment, purchase_ids=purchase_ids, sim_run_id=sim_run_id
        )
        finance_row = finance.build(
            commitment,
            target_state_date=target_state_date,
            purchase_ids=purchase_ids,
        )
        # 🔴 **재무와 같은 매핑을 준다** (`#311` · 물류 요청 2026-09-06). 물류가 도착일에
        #    `purchase_items` 의 권위값을 읽으려면 매입 원장을 가리키는 ID 가 필요하고,
        #    물류는 그 ID 를 만들지도 파싱하지도 않는다.
        #
        # ★ **`persist_purchases` 가 먼저 돈다** (아래). 물류가 저장하는 `purchase_id` 가
        #   가리키는 부모 행은 **같은 트랜잭션 안에서 이미 서 있다.**
        #
        # 🔴 **이미 열린 다음 날들로 전파하지 않는다** (물류 W3-3 · 2026-09-09).
        #
        #    이 전파는 **물류가 입고 예정을 날짜별 fixture JSON 에 복제해 두던 구조**
        #    하나 때문에 있었다. 그 구조에서는 다음 날이 이미 열려 있으면 그 행이
        #    승인을 모른 채 굳었고, 그래서 승인분을 열린 날마다 다시 실어야 했다.
        #
        #    ```text
        #    ~W3-2   in_transit_json 을 날마다 복제       → 전파가 필요했다
        #    W3-3~   inbound_schedules 1행 · 날짜로 질의  → 전파할 것이 없다
        #    ```
        #
        # 🔴 **이제는 전파가 오히려 승인을 깨뜨린다.** 물류가 같은 `inbound_id` 를
        #    다른 `created_as_of` 로 두 번 받으면 `ScheduleConflict` 로 멈춘다 —
        #    일정 한 건이 «언제 장부에 섰나» 를 둘 가질 수 없기 때문이다.
        #    (실측 2026-09-09: 같은 회차를 02-09 · 02-10 에 실으면 그 승인이 FAILED.)
        #
        # ★ **`opened_days_after` 함수는 남긴다.** 이 호출 하나만 걷는다 — 그 함수는
        #   마스터 소유이고, 다른 사실에 쓸 자리가 생길 수 있다.
        #
        # ⚠️ `carried_forward` 는 이제 늘 비어 있고 `carried_forward_status` 는 `OK` 다.
        #    회신 모양을 바꾸지 않는다 — 읽는 쪽이 있고, 이번 판의 일이 아니다.
        logistics_rows = tuple(
            logistics.build(
                commitment,
                target_state_date=target_state_date,
                purchase_ids=purchase_ids,
            )
        )
        carry_status = "OK"
    except Exception as exc:  # noqa: BLE001 - 전이 실패가 적재된 결정을 지우면 안 된다.
        return TransitionOut(status="FAILED", reason=f"전이 계산 실패: {exc}")

    open_connection = core_db.connection if borrow is None else borrow
    with open_connection() as conn:
        try:
            # 🔴 **재무보다 먼저다.** `payables.purchase_id` 가 `purchases` 를 참조하는
            #    FK 라 부모 행이 먼저 서야 한다.
            persist_purchases(conn, ledger_rows)
            finance.persist(conn, finance_row)
            logistics.persist(conn, logistics_rows)
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - 전이 실패가 적재된 결정을 지우면 안 된다.
            conn.rollback()
            return TransitionOut(status="FAILED", reason=f"전이 적재 실패: {exc}")
    return TransitionOut(
        status="APPLIED",
        parts=list(PARTS),
        # 🔴 **열린 날이 아니라 실제로 쓴 날이다** (`#381`).
        # ⚠️ 늘 비어 있다 — 전방 전파가 없어졌다 (W3-3). 회신 칸은 남긴다.
        carried_forward=[],
        carried_forward_status=carry_status,
    )
