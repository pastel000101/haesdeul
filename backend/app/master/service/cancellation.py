"""
cancellation.py — 승인을 물린다. `service/transition.py` 의 `apply_approval` 과 대칭인
트랜잭션 경계.

취소 없이 번복만 하면 앞 승인의 장부가 남는다.

```text
승인 A(기본)  decision_seq 1 → purchases D1 · payables AP-…-1 · unsettled += A
번복 B(보수)  decision_seq 2 → purchases D2 · payables AP-…-2 · unsettled += B

decision_seq 가 달라 purchase_id 도 달라 ON CONFLICT 가 안 걸린다 — 둘 다 남는다.
```

```text
사용자가 한 것       기본을 취소하고 보수를 골랐다
화면이 보여 주는 것   보수 하나          (current_commitment · is_current)
장부에 있는 것        기본 + 보수 둘
```

`#290` 이 승인 위 승인을 막는 것만으로는 부족하다. `REJECT_ALL` · `REQUEST_CHANGE` 를
끼우면 뚫린다. 그래서 되돌리는 경로가 필요하다.

지우지 않고 적는다.

```text
지우면   그 승인이 있었다는 사실이 사라진다
적으면   "승인했고 취소했다" 가 이력에 남는다
```

`master_decisions` 가 append-only 인 이유가 그것이고 원장도 같다. 이 모듈은 어디서도
DELETE 를 내지 않는다 — 상태 칸을 `CANCELLED` 로 적을 뿐이다.

다섯 자리가 한 트랜잭션이다.

  ```text
  ① master_decisions   CANCEL 결정                      마스터 (호출자가 적재)
  ② purchases          settlement_status = CANCELLED    마스터 (`repository/ledger.py`)
  ③ payables           CANCELLED + 취소금액              재무 (`#302`)
  ④ finance_states     unsettled 역분개                  재무 (`#302`)
  ⑤ 물류 fixture       in_transit · confirmed_inbound    물류
  ```

  다섯이 다 되거나 다 안 되어야 한다. 재무만 물리고 물류가 남으면 "돈은 안 내는데
  물건은 오는" 장부가 된다 — `apply_approval` 이 막는 것과 같은 종류다.

취소일과 상태 날짜는 다른 값이고 둘 다 싣는다(재무 회신 `§4`).

  ```text
  commitment.as_of   원 승인일    — 취소일로 쓰면 안 된다
  cancelled_on       취소 사건일
  target_state_date  cancelled_on + 1 calendar day
  ```

  승인과 같은 규칙이다 — "사건이 일어난 날 + 1일". 승인도 취소도 사건이고, 사건은
  다음 날 상태에 나타난다.

  `target_state_date - 1` 로 역산하지 않는다. 지금은 그 뺄셈이 맞지만 규칙이 바뀌는 날
  취소일이 조용히 따라 틀린다. 같은 사실을 두 곳에서 만들지 않는다.

  과거 상태를 고치지 않는다. 승인 01-05 · 취소 01-07 이면 01-06 · 01-07 의 미지급은
  그대로 두고 01-08 부터 뺀다 — 그때는 실제로 미지급이 있었다.

제약: 입고된 뒤에는 물리지 못한다. 물건이 창고에 있으면 취소가 아니라 반품이다. 재무의
"`SETTLED` 는 fail-closed" 와 같은 성격이고, 판정은 각 파트가 한다 — 이 모듈은 파트가
거절하면 통째로 롤백한다.

매입 id 계산은 `domain/purchase_ids.py`, 취소 등록소는 `registry/cancellation.py`, 응답
모양은 `schemas/cancellation.py` 에 있다.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.contracts.commitment import ApprovedCommitment
from app.core import db as core_db
from app.master.domain.ledger import sim_run_id_for
from app.master.domain.purchase_ids import purchase_ids_of
from app.master.readmodel.ledger import get_burn_in
from app.master.registry.cancellation import cancellation_missing, registered_cancellations
from app.master.registry.sim_run_binding import bind_sim_run
from app.master.registry.transition import PARTS
from app.master.repository.ledger import cancel_purchases
from app.master.schemas.cancellation import CancellationOut


def financing_mode_of(commitment: ApprovedCommitment, *, sim_run_id: str | None) -> str:
    """이 승인이 속한 실행의 재무 축.

    실행 행에 이미 적힌 값이다. `sim_runs.financing_mode` 를 마스터 조회
    (`readmodel/ledger.py` 의 `get_burn_in`)로 읽는다 — 지어내는 값이 아니라서 재무가
    요청한 "호출자가 축을 명시" 가 성립한다.

    축은 `domain/ledger.py` 의 `sim_run_id_for` 에서 온다. 그 함수가 받은 축을 돌려주므로
    여기도 부르는 쪽이 지정한 같은 실행을 가리킨다.

    :param sim_run_id: 물릴 승인이 앉은 실행. 기본값이 없다 — 취소가 승인과 다른 실행의
        행을 물리면 장부가 양쪽 다 틀린다.
    :raises ValueError: 축을 못 받았을 때.
    :raises LookupError: 그 실행을 못 찾을 때. 지어내지 않는다.
    """
    축 = sim_run_id_for(commitment, sim_run_id=sim_run_id)
    run = get_burn_in(축)
    mode = run.get("financing_mode")
    if not isinstance(mode, str) or not mode.strip():
        raise LookupError(f"sim_runs.financing_mode 를 읽을 수 없다 ({축}) — 축을 지어내지 않는다")
    return mode


def undo_approval(
    commitment: ApprovedCommitment,
    *,
    cancelled_on: date,
    sim_run_id: str | None,
    borrow: core_db.Borrow | None = None,
) -> CancellationOut:
    """승인 하나를 다섯 자리에서 한 트랜잭션으로 물린다.

    실패 처리: `apply_approval` 과 대칭이다 — 예외를 밖으로 내지 않고 값으로 돌려준다.
    취소 실패가 적재된 결정을 지우면, 사람이 취소를 눌렀다는 사실이 사라진다.

    `cancelled_on` 이 승인일보다 앞설 수 없다. 앞서면 "승인하기 전에 취소했다" 가 장부에
    남는다 — 그건 날짜를 잘못 넘긴 것이지 사건이 아니다.

    :param cancelled_on: 취소 사건일. `commitment.as_of`(승인일)와 다를 수 있다.
    :param sim_run_id: 물릴 승인이 앉은 실행. 기본값이 없고 상수로 메우지 않는다 —
        `apply_approval` 과 대칭이다. 못 받으면 `FAILED` 가 사유를 싣는다.
    """
    if cancelled_on < commitment.as_of:
        return CancellationOut(
            status="FAILED",
            reason=(
                f"취소일({cancelled_on.isoformat()})이 승인일"
                f"({commitment.as_of.isoformat()})보다 앞선다 — 날짜가 잘못 왔다"
            ),
        )

    absent = cancellation_missing()
    if absent:
        # 어댑터 미등록은 오류가 아니다. 그 부서가 취소를 하지 않는다는 뜻이고, 그때
        # 마스터가 자기 원장만 물리면 반쪽 취소가 된다.
        return CancellationOut(
            status="NOT_APPLIED",
            reason=f"취소 어댑터 미등록: {', '.join(absent)}",
            missing=list(absent),
        )

    try:
        # 등록소가 든 축이 아니라 이 취소의 축으로 묶는다(`#531`). 취소는 승인이 앉은
        # 실행을 물리는 일이다 — 등록소가 프로세스 시작 때 든 상수로 물리면 남의 실행
        # 장부를 물린다.
        #
        # 커넥션을 열기 전이다. 축을 못 받으면 트랜잭션을 시작하지도 않는다 — 아래
        # `financing_mode_of` 와 같은 자리, 같은 규율이다.
        adapters = {
            part: bind_sim_run(impl, sim_run_id)
            for part, impl in registered_cancellations().items()
        }
        # 커넥션을 열기 전에 읽는다. 축을 못 읽으면 트랜잭션을 시작하지도 않는다 —
        # `apply_approval` 이 build 를 커넥션 밖에서 부르는 것과 같은 규율이다.
        financing_mode = financing_mode_of(commitment, sim_run_id=sim_run_id)
    except Exception as exc:  # noqa: BLE001 - 축을 못 읽은 것도 값으로 돌려준다.
        return CancellationOut(status="FAILED", reason=f"재무 축을 못 읽었다: {exc}")
    # 취소일 + 1일이다. 승인과 같은 규칙이고, 부서가 다시 계산하지 않게 마스터가 실어
    # 준다(재무 요청).
    target_state_date = cancelled_on + timedelta(days=1)
    purchase_ids = purchase_ids_of(commitment)

    open_connection = core_db.connection if borrow is None else borrow
    with open_connection() as conn:
        try:
            # 매입 원장을 먼저 물린다. 승인이 부모를 먼저 세운 것과 같은 순서다 — 읽는
            # 사람이 두 경로를 나란히 볼 수 있어야 한다.
            cancelled = cancel_purchases(conn, purchase_ids.values())
            for part in PARTS:
                adapters[part].cancel(
                    conn,
                    commitment=commitment,
                    cancelled_on=cancelled_on,
                    target_state_date=target_state_date,
                    purchase_ids=purchase_ids,
                    financing_mode=financing_mode,
                )
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - 취소 실패가 적재된 결정을 지우면 안 된다.
            conn.rollback()
            return CancellationOut(status="FAILED", reason=f"취소 적재 실패: {exc}")
    return CancellationOut(
        status="CANCELLED", parts=list(PARTS), cancelled_purchases=cancelled
    )
