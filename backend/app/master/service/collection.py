"""
collection.py — **채권이 실제로 돈으로 들어오는 자리.** 다섯째 등록소.

🔴 **부르는 곳이 0건이다** (실측 2026-09-07).

```text
app/finance/collection.py :196   apply_explicit_collection(conn, CollectionEvent(...))
그런데 이것을 부르는 production 호출자가 **0건**이다 — 검사 파일 하나가 전부다
```

★★ **재무 경계는 이미 서 있다.** 누적 target 을 delta 로 접는 전이도, 역행·초과를
  막는 불변식도, 멱등도 `app/finance/collection.py` 안에 다 있다. **없는 것은 그것을
  날마다 부르는 자리**이고, 이 등록소가 그 자리를 만든다.

⚠️ `register_inbound` 호출이 0건이던 것(`#337`)과 **같은 모양**이다 — 구현이 다 섰는데
  배선이 없어 매일 조용히 아무 일도 안 일어났다.

---

★ **재무 결정 2026-09-07.**

```text
Finance   실제 수금 사건의 정본 의미 소유 — receivable_id · collection_date
          · cumulative target_received_total_krw · (sim_run_id, financing_mode) 축
Master    날짜에 해당하는 사건을 **운반하고 호출**한다.
          수금 대상이나 수금액을 **스스로 결정하지 않는다**
Sales     계약 결제조건·채권 발생의 상업적 원천 — "오늘 얼마 입금됐다"는 안 만든다
```

🔴 **due_date 경과 ≠ 자동 수금. fixture 를 만들 때도 그렇다.**

  ⚠️ *"기일이 지났으니 들어왔겠지"* 는 **마스터가 재무 사실을 발명하는 것**이다. 실제
    입금은 안 됐는데 장부에 현금이 늘고, 그 현금으로 매입 판단이 돈다.

🔴 **마스터는 `as_of` 만 준다 — `InboundExecution` 과 같은 이유다.** *"오늘 무엇이
   수금됐나"* 를 마스터가 모르고 **재무가 읽어야** 안다.

---

★ **하루의 걸음이 각자 멱등하고 각자 실패한다.**

```text
① open_day(as_of)          상태 행을 보장한다        ← 먼저 (적을 자리가 있어야 한다)
② receive_arrivals(as_of)  도착분을 실제로 받는다
③ collect_receipts(as_of)  수금 사건을 반영한다       ← 여기
④ run_procurement(as_of)   그 위에서 판단한다
```

  ⚠️ **`run_procurement` 안에 넣지 않는다.** 넣으면 판단 한 번이 현금을 움직이고
    *"같은 `as_of` 로 백번 돌려도 같은 답"* 이 깨진다 — 개장·입고를 판단 밖에 둔
    이유와 같다.

🔴 **날마다다. 실행일이 아니다.** 입금은 토요일에도 찍힌다 — `is_open` 은 **시장이
  서는가**이지 은행이 여는가가 아니다. `open_day` · `receive_arrivals` 와 같은 결이다
  (`#240` — *"실행일은 평일만, 경과일수는 달력일"*).

⚠️ **파트가 재무 하나다.** 수금이 현금을 늘리면 판매 쪽 채권 잔액도 움직여야 할 수
  있는데, **그 판단은 재무 몫**이라 여기서 정하지 않는다. 등록소를 파트별로 두는
  이유가 그것이다 — 판매가 필요하다고 하면 한 줄로 붙는다.

★ 2026-09-30 재구성 BL-018: `master/collection.py` 에서 옮겼다. 역할이 다른 부분은 갈랐다 —
  `registry/collection.py`; `schemas/collection.py`. 무엇이 어디로 갔는지는 설계서 대응표 `master/`
  절.

─── 재무 수금 파트 본문 (`collect_finance_receipts`) ───────────────────────────────────

★ 2026-09-30 재구성 BL-018: 등록소에 꽂히는 재무 수금 구현(`master/finance_collection.py` 의
  `FinanceCollectionAdapter.collect`)의 본문을 이 파일로 옮겼다. 등록소 Protocol 표면(생성 인자
  `sim_run_id` · 대역 자리 `read_axis` · `load_events`)은 `adapters/finance_parts.py` 에 그대로
  있고,
  표면은 이 함수를 부른다. 아래는 옛 파일의 머리말이다(그대로 — «이 파일» 은 옛 파일을 가리킨다).

  finance_collection.py — 마스터 `CollectionSource` 를 재무 수금 구현체에 잇는 배선.

  🔴 **이 파일이 하는 일은 축을 맞춰 주는 것뿐이다.**

  ```text
  마스터 Protocol        collect(conn, *, as_of)
  재무 구현체            FinanceCollectionSource(sim_run_id, financing_mode, source)
                         .collect(conn, *, as_of)
  ```

    Protocol 은 `as_of` 만 나르는데 재무 구현체는 **생성 인자로 축 둘**을 받는다
    (`app/master/collection.py` 의 `CollectionSource` 가 그렇게 적어 뒀다 — *"어느
    실행의 장부인가는 실행 정체성이라 어댑터 생성 인자로 온다"*). 그 둘을 어디서
    가져오는가가 이 파일의 전부다.

  ---

  🔴 **`sim_run_id` 는 마스터가 정하고 `financing_mode` 는 마스터가 고르지 않는다.**

  ```text
  sim_run_id       🟢 **마스터가 정한다** — 값의 주인은 ledger_repository.BURN_IN_SIM_RUN_ID 하나
  financing_mode   🔴 **마스터 축이 아니다** — 재무 축 (sim_run_id, as_of, financing_mode) 의 것
  ```

    ⚠️ **여기에 `financing_mode` 를 상수로 박으면 안 된다.** 실측으로 `finance_states`
      에 `LOAN_BASELINE` 252행과 `BASE_NO_LOAN` 2행이 **실제로 공존한다**. 마스터가
      하나를 골라 박으면 *"무차입 상태가 대출 baseline 자리에 조용히 들어오는"* 사고가
      나고, 그 사고는 에러 없이 숫자만 바꾼다 — `app/finance/readmodel/finance_state.py` 의
      `get_finance_runtime_axis` 가 그 문장을 이미 적어 뒀다.

  ★ **그래서 물어본다.** `get_finance_runtime_axis()` 가 재무 축의 주인이고, 이
    어댑터는 그 답을 **그대로 실어 보낸다.** 고르지 않는다.

  🔴 **임포트 시점에 DB 를 읽지 않는다.** `app/main.py` 의 등록소 넷이 다 그렇다 —
    축 조회는 `collect()` **안에서** 일어난다. 배선이 DB 를 요구하기 시작하면 앱이
    뜨는 조건이 조용히 늘어난다.

  ---

  🔴 **축의 `sim_run_id` 가 마스터 것과 다르면 막는다 (fail-closed).**

    재무가 읽은 축이 다른 실행을 가리키는데 마스터 값으로 덮어 쓰고 진행하면 **남의
    실행 장부에 수금을 적는다.** 그것은 에러 없이 남의 현금을 늘린다.

    ⚠️ **덮어 쓰는 것이 더 나빠 보이지 않는 것이 함정이다.** `FinanceCollectionSource`
      는 자기가 받은 축으로만 사건을 고르므로 아무 예외 없이 조용히 돈다 — 그래서
      여기서 세운다.

  ★ **`BLOCKED` 다.** `NOTHING_DUE` 로 접으면 *"오늘은 들어올 게 없었다"* 로 읽히고,
    들어왔어야 할 현금이 장부에 없는 채로 매입 판단이 돈다
    (`app/master/collection.py` 의 다섯 갈래 표에서 축 불일치가 `BLOCKED` 다).

  ---

  ⚠️ **축이 모호하면 삼키지 않는다.** `get_finance_runtime_axis()` 는 실행이 여럿이면
    `FinanceDataNotReady("finance_runtime_axis_ambiguous")` 를 던진다. 그 사유를
    `BLOCKED` 로 접되 **무엇이 모호했는지를 문장에 남긴다** — 접기만 하고 사유를 버리면
    화면에는 *"막혔다"* 만 남고 고칠 곳이 사라진다.

  ---

  🔴 **사건은 호출 시점에 표에서 읽는다** (2026-09-08 · `master_collection_events`).

    전에는 이 어댑터가 `source: DeterministicCollectionFixtureSource` 를 **필드로**
    들고 있었다. 그러면 사건 목록이 **배선 시점에 고정**되고, 표에 한 줄 넣어도 앱을
    다시 띄우기 전까지는 아무 일도 안 일어난다.

  ```text
  ① 재무 축을 읽는다                     ← 지금 그대로
  ② sim_run_id 불일치면 BLOCKED          ← 지금 그대로
  ③ 그 축의 사건을 표에서 읽는다          ← 여기
  ④ 재무 fixture 원천으로 감싸 위임한다
  ```

    ⚠️ **③ 이 실패하면 `BLOCKED` 다.** `()` 로 접으면 표가 안 서 있거나 DB 가 끊긴 날이
      *"오늘은 들어올 게 없었다"* 로 읽힌다 — `collection_events.read_collection_events`
      가 예외를 그대로 올리는 이유가 그것이고, 접는 자리는 여기다.

  🔴 **표가 비어 있다** (2026-09-08 실측 0행). **이 판은 자리를 만들 뿐 사건을 만들지
    않는다.** 무엇을 사실로 둘지는 팀 결정이고, 재무가 *"due_date 경과를 수금으로 읽지
    않는다"* 로 그은 선이 그 이유다. **낸 것과 도는 것은 다르다.**
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from app.contracts.parts import CollectionPartOut
from app.core import db as core_db
from app.finance.schemas.collections import CollectionEvent, DeterministicCollectionFixtureSource
from app.finance.schemas.data_port import FinanceDataNotReady
from app.finance.schemas.finance_state import FinanceRuntimeAxis
from app.finance.service.collections import FinanceCollectionSource
from app.master.registry.collection import PARTS, missing, registered
from app.master.registry.sim_run_binding import bind_sim_run
from app.master.schemas.collection import CollectionOut
from app.master.service.day_gate import check_day_gate

# ── 경계 ────────────────────────────────────────────────────────────────


def collect_receipts(
    as_of: date, *, borrow: core_db.Borrow | None = None, sim_run_id: str
) -> CollectionOut:
    """`as_of` 의 수금 사건을 **한 트랜잭션으로** 반영한다.

    ★ **`open_day` 다음이다.** 상태 행이 있어야 수금을 적을 자리가 있다. 다만 **함수는
      따로다** — 묶으면 실패 원인이 뭉개진다.

    ★ **예외를 밖으로 내지 않는다.** `receive_arrivals` · `apply_approval` 과 같다 —
      수금 실패가 판단을 멈추면 그날 하루가 통째로 서고, 그건 수금 하나보다 크다.

    🔴 **예외 전파 여부와 후속 진행 여부는 다른 물음이다.**

      ```text
      예외를 안 올린다        🟢 이 함수의 계약
      그러니 판단을 계속한다   🔴 **그런 뜻이 아니다**
      ```

      ⚠️ **수금이 `FAILED` · `BLOCKED` 인데 매입 판단을 계속하면 가용 현금이 실제보다
        적게 반영된 상태로 판단한다.** 들어왔어야 할 돈이 장부에 없는 채로 *"현금이
        없으니 사지 말자"* 가 나온다.

      ★ **부르는 쪽이 정한다.** 이 함수는 상태를 값으로 돌려주고, `run_procurement`
        진행 여부는 그것을 본 orchestration 의 결정이다.

    ⚠️ **달력일이다.** 입금은 토요일에도 찍힌다. 실행일 달력을 쓰지 않는다.

    ---

    🔴 **개장 Gate 를 먼저 본다** (`receive_arrivals` 와 같은 이유).

      순서를 문장으로만 적어 두면 그것을 지키는 책임이 부르는 쪽에 통째로 있고, 안
      지킨 날 `NOTHING_DUE` 가 나가 *"오늘은 들어올 게 없었다"* 로 읽힌다.

      ```text
      Gate BLOCKED   →  NOT_OPENED     수금할 것이 있는지조차 안 묻는다
      Gate PASS      →  평소대로
      ```

      ★ **`check_day_gate` 는 열지 않는다. 묻기만 한다.** 여기서 `open_day` 를 부르면
        수금이 개장의 부작용이 되고, `router.py` 가 개장에 대해 적어 둔 *"명시적
        호출이다. 실행의 부작용이 아니다"* 를 수금이 어긴다.

      ⚠️ **미등록은 PASS 다** (`day_gate` 계약). 정본 표가 없는 환경에서 이 Gate 가
        수금을 막지 않는다 — 없는 것과 안 열린 것은 다르다.

    :param sim_run_id: 어느 실행의 장부인가 (`#531` 후속). 🔴 **기본값이 없다**
                    (2026-09-14). 번인 상수로 메우면 축을 안 준 호출이 조용히
                    번인 장부에 쓴다. 걷기는 `run_scheduled_day` 가, 라우터는
                    요청이 준 축을 싣는다.
    """
    # 🔴 **관문에도 이번 호출의 축을 넘긴다** (`#539` 후속). 안 넘기면 관문이 번인
    #    축으로 어댑터를 묶고, 걷기 실행에서 열린 날을 **안 열린 날**로 읽는다.
    gate = check_day_gate(as_of, borrow=borrow, sim_run_id=sim_run_id)
    if gate.gate == "BLOCKED":
        return CollectionOut(
            as_of=as_of,
            status="NOT_OPENED",
            reason=gate.reason,
            # ★ **해석하지 않고 옮긴다.** 무엇을 해야 하는지는 개장이 아는 사실이고,
            #   수금이 다시 판정하면 같은 사실의 주인이 둘이 된다.
            next_action=gate.next_action,
        )

    absent = missing()
    if absent:
        # ★ **미등록은 오류가 아니다.** 그 파트가 아직 수금을 실행하지 않는다는 뜻이고,
        #   *"오늘 들어올 것이 없다"* 와 다른 사실이다.
        return CollectionOut(
            as_of=as_of,
            status="NOTHING_DUE",
            reason=f"수금 실행 미등록: {', '.join(absent)}",
            missing=list(absent),
        )

    open_connection = core_db.connection if borrow is None else borrow
    with open_connection() as conn:
        try:
            # 🔴 **등록소가 든 축이 아니라 이번 호출의 축으로 묶는다** (`#531` 후속).
            #    `FinanceCollectionAdapter` 는 그 축을 재무 축과 대조해 fail-closed 한다 —
            #    등록소가 프로세스 시작 때 든 상수로 쓰면 **매입 원장만 새 실행에
            #    앉고 이쪽은 번인에 남는다.**
            #
            # ★ **`try` 안이다.** 축이 비면 `bind_sim_run` 이 막는데, 그 실패도 예외로
            #   올라가지 않고 아래 `except` 가 `FAILED` + 사유로 옮긴다 — 수금이
            #   그날을 통째로 세우면 안 된다는 이 함수의 계약 그대로다.
            adapters = {
                part: bind_sim_run(impl, sim_run_id) for part, impl in registered().items()
            }
            results = [adapters[part].collect(conn, as_of=as_of) for part in PARTS]
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - 수금 실패가 그날을 통째로 세우면 안 된다.
            conn.rollback()
            return CollectionOut(as_of=as_of, status="FAILED", reason=f"수금 실행 실패: {exc}")

    return _aggregate(as_of, results)


def _aggregate(as_of: date, parts: list[CollectionPartOut]) -> CollectionOut:
    """파트 결과를 전체 어휘로 취합한다.

    ```text
    BLOCKED 가 하나라도    → BLOCKED     들어올 게 있었는데 못 받았다
    COLLECTED 가 하나라도  → COLLECTED
    전부 NOTHING_DUE       → NOTHING_DUE
    ```

    🔴 **순서가 계약이다.** `BLOCKED` 를 먼저 보는 이유는 그것이 **사람이 봐야 하는
       사실**이기 때문이다 — `COLLECTED` 뒤로 밀면 *"오늘 받았다"* 로 지나간다.
    """
    blocked = [part for part in parts if part.status == "BLOCKED"]
    if blocked:
        return CollectionOut(
            as_of=as_of,
            status="BLOCKED",
            reason=f"수금할 것이 있는데 막혔다: {', '.join(part.part for part in blocked)}",
            parts=parts,
        )
    if any(part.status == "COLLECTED" for part in parts):
        return CollectionOut(as_of=as_of, status="COLLECTED", parts=parts)
    return CollectionOut(as_of=as_of, status="NOTHING_DUE", parts=parts)


def collect_finance_receipts(
    conn: Any,
    *,
    as_of: date,
    sim_run_id: str,
    read_axis: Callable[..., FinanceRuntimeAxis],
    load_events: Callable[..., tuple[CollectionEvent, ...]],
) -> CollectionPartOut:
    """재무 축을 읽어 `FinanceCollectionSource` 를 세우고 위임한다(마스터 수금 단계의 재무 파트).

    ★ 받은 연결(마스터 수금 단계의 연결)로 재무 쓰기가 돈다 — 빌리지도 commit 하지도 않는다.
      사건 읽기(`load_events`)는 그 연결을 쓰지 않는다(종전 그대로 — 기본값은 자기 연결).
    """
    try:
        axis = read_axis(sim_run_id=sim_run_id)
    except (FinanceDataNotReady, LookupError, ValueError) as exc:
        # ★ **사유를 그대로 옮긴다.** `finance_runtime_axis_ambiguous` 가 여기서
        #   사라지면 *"막혔다"* 만 남고 무엇이 모호했는지가 없어진다.
        return CollectionPartOut(
            part="finance",
            status="BLOCKED",
            reason=f"재무 축을 읽지 못했다: {exc}",
        )

    if axis["sim_run_id"] != sim_run_id:
        # 🔴 **덮어 쓰지 않는다.** 남의 실행 장부에 조용히 수금을 적는 자리다.
        return CollectionPartOut(
            part="finance",
            status="BLOCKED",
            reason=(
                "실행 축이 다르다: 마스터 sim_run_id="
                f"{sim_run_id!r}, 재무 축 sim_run_id={axis['sim_run_id']!r}"
            ),
        )

    try:
        events = load_events(
            sim_run_id=sim_run_id,
            financing_mode=axis["financing_mode"],
        )
    except Exception as exc:  # noqa: BLE001 - 조회 실패를 `()` 로 접지 않는다.
        # 🔴 **없는 것과 못 읽은 것은 다르다.** `()` 로 접으면 표가 안 서 있거나
        #   DB 가 끊긴 날이 *"오늘은 들어올 게 없었다"* 로 읽히고, 들어왔어야 할
        #   현금이 장부에 없는 채로 매입 판단이 돈다.
        return CollectionPartOut(
            part="finance",
            status="BLOCKED",
            reason=f"수금 사건을 읽지 못했다: {type(exc).__name__}: {exc}",
        )

    return FinanceCollectionSource(
        sim_run_id=sim_run_id,
        # 🔴 **고르지 않는다. 재무가 읽은 값 그대로다.**
        financing_mode=axis["financing_mode"],
        # ★ 사건 원천의 모양은 **재무 것**이다. 마스터는 읽어 온 사건을 그 그릇에
        #   담아 넘길 뿐이고, 그날 것을 고르는 일은 그쪽이 한다.
        source=DeterministicCollectionFixtureSource.from_events(
            events,
            source_ref="master_collection_events",
        ),
    ).collect(conn, as_of=as_of)
