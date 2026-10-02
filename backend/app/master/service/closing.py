"""하루 마감 — 하루 실행의 마지막 단계에서 마감 등록소에 걸린 파트를 부른다.

역할: 마스터는 "그날을 닫아 달라" 고 부르고 결과를 어휘로 받아 적는다. 그날 숫자를
계산하고 `daily_closings` 에 적재하는 것은 재무다(`app/finance/service/closing.py`).
앱이 기동할 때 `registry/bootstrap.py` 가 재무 어댑터(`FinanceClosingAdapter`)를
마감 등록소(`registry/closing.py`)에 건다.

이 모듈은 숫자를 계산하지 않고, 잔액을 추정하지 않고, `daily_closings` 에 직접 쓰지
않는다. 마스터가 계산하면 조정자가 부서 일을 겸하게 되고, 같은 값(예:
`purchase_cash_out_krw`)의 주인이 둘이 되어 재무와 갈리는 날 오류 없이 손익만
틀린다. 그래서 `ClosingPartOut` 에는 금액 칸이 없고 무엇이 닫혔는지의 키만
나른다. 금액의 기준은 `daily_closings` 한 곳이다(`ReceivablePartOut.issued` 가
`receivable_id` 만 나르는 것과 같은 규율). `tests/master/test_closing.py` 가 원문과
AST 로 이 파일에 산술 연산이 없는지 검사한다.

## 1. 하루 순서

```text
개장 → 입고 → 채권 → 수금 → [장부 관문] → 판단 → 출고 → 마감
```

마감은 출고 뒤다. 출고가 재고를 움직이므로 출고 앞에서 닫으면 그날 재고가 마감 뒤에
바뀌고, `inventory_qty_kg` 가 그날 장부와 어긋나도 오류는 나지 않는다.

## 2. 어휘 — 채권 등록소와 같은 다섯

```text
CLOSED         닫았다
NOTHING_DUE    닫을 것이 없다 (그날 움직임이 없었다) — 또는 미등록이다
BLOCKED        장부가 안 서서 못 닫는다
NOT_OPENED     그날이 안 열렸다
FAILED         부르다 실패했다 — 아무것도 바뀌지 않았다
```

새 어휘를 짓지 않고 `ReceivableOut.status` 의 다섯을 그대로 쓴다. «마감할 것이 없다»
와 «마감이 막혔다» 는 다르다.

```text
NOTHING_DUE   그날 닫을 움직임이 실제로 없었다 — 정상이다
BLOCKED       장부가 안 서서 못 닫았다 — 사람이 봐야 한다
NOT_ATTEMPTED 단계를 아예 안 탔다 (`DayRunOutcome` 이 든다) — 안 돈 날이다
```

이 셋을 접으면 장부가 안 선 날과 아무 일도 없던 날의 빈 손익 곡선이 화면에서 같아
보인다. 앞은 고칠 것이 있고 뒤는 없다.

## 3. 제약

```text
① 마감이 실패해도 그날 하루 실행 결과를 바꾸지 않는다
   (try_save_run 이 `try_` 인 이유와 같다 — 이력 때문에 운영이 멈추면 안 된다)
② 장부 관문이 막은 날은 BLOCKED 이고 닫지 않는다
③ 같은 날을 두 번 실행해도 마감 행이 두 벌 쌓이지 않는다
④ sim_run_id 는 인자로 받아 넘긴다 — 상수로 박지 않는다
```

③ 의 근거는 표의 키다.

```text
database/schema/finance/daily_closings.sql
  ADD CONSTRAINT daily_closings_pkey PRIMARY KEY (sim_run_id, close_date)
```

그래서 이 모듈이 어댑터에 넘기는 것은 그 두 값, `as_of` 와 `sim_run_id` 다. 마스터가
`closing_id` 같은 값을 따로 지으면 같은 날이 두 벌 쌓인다. 제약이 있다는 사실과 그
제약이 이 모듈이 넣는 키를 막는다는 사실은 다르므로, 키는 DDL 에서 확인했고 검사가
그 줄을 다시 읽는다.

멱등: 이 모듈은 어댑터를 파트마다 정확히 한 번만 부른다. 두 번 부르고 어댑터의
멱등에 기대지 않는다.
"""

from __future__ import annotations

from datetime import date

from app.contracts.parts import ClosingPartOut
from app.core import db as core_db
from app.master.registry.closing import PARTS, missing, registered
from app.master.schemas.closing import ClosingOut
from app.master.service.day_gate import check_day_gate

# ── 경계 ────────────────────────────────────────────────────────────────


def close_day(
    as_of: date,
    *,
    sim_run_id: str,
    ledger_gap: str = "",
    borrow: core_db.Borrow | None = None,
) -> ClosingOut:
    """`as_of` 를 한 트랜잭션으로 닫는다. 숫자는 재무가 낸다.

    하루의 맨 끝 단계다. 출고가 재고를 움직이므로 그 앞에서 닫으면 그날 재고가
    마감 뒤에 바뀐다.

    실패 처리: 예외를 밖으로 내지 않는다. `receive_arrivals` · `collect_receipts` ·
    `issue_receivables` 와 같다 — 마감 실패가 그날 하루 실행 결과를 바꾸면 안 된다.
    `try_save_run` 이 `try_` 인 이유와 같다: 이력 때문에 운영이 멈추면 안 된다.

    다만 `sim_run_id` 가 비면 예외를 낸다(`read_procurement_boundary` 와 같은 태도).
    빈 축은 그날의 사실이 아니라 호출 연결의 오류이고, 조용히 전체로 바꾸면 남의
    실행 장부에 이 날의 마감이 앉는다. 부르는 쪽(`scheduler._stage`)이 그것을
    `FAILED` 로 바꾸므로 하루 실행은 멈추지 않는다.

    판정 순서가 계약이다.

      ```text
      ① 개장 Gate 가 BLOCKED   →  NOT_OPENED   닫을 것이 있는지조차 안 묻는다
      ② ledger_gap 이 있다      →  BLOCKED      장부가 안 서서 못 닫는다
      ③ 미등록                  →  NOTHING_DUE  + missing
      ④ 그 밖                   →  어댑터에게 묻는다
      ```

      ① 이 ② 보다 앞이다. 하루가 안 열린 날은 장부 관문에 오지도 않는다 — 순서를
      뒤집으면 "안 열린 날" 이 "장부가 안 섰다" 로 접힌다.

      `check_day_gate` 는 열지 않고 묻기만 한다. 여기서 `open_day` 를 부르면 마감이
      개장의 부작용이 된다. 미등록은 PASS 다(`day_gate` 계약).

    :param sim_run_id: 어느 실행의 장부를 닫는가. 인자로 받고 여기에 상수를 박지
        않는다 — 박으면 실행이 둘이 되는 날 이 파일을 고쳐야 하고, 값의 주인이
        `master/domain/sim_run.py` 의 `BURN_IN_SIM_RUN_ID` 하나라는 사실이 깨진다.
    :param ledger_gap: 장부 관문이 막았으면 그 사유. 빈 문자열이면 안 막혔다는
        뜻이다. 여기서 문장을 다시 짓지 않고 부르는 쪽(`scheduler._ledger_gap_note`)이
        만든 문장을 그대로 받는다. 두 벌이 되면 한쪽만 고치는 날 화면과 이력이 갈린다.
    """
    if not sim_run_id.strip():
        raise ValueError(
            "sim_run_id 없이 하루를 닫을 수 없다 — 어느 실행의 장부인지 없으면"
            " 마감 행이 어디에 앉을지가 정해지지 않는다"
        )

    # 관문에도 이번 마감의 축을 넘긴다. 안 넘기면 관문이 번인 축으로 어댑터를 묶고,
    # 걷기 실행에서 열린 날을 안 열린 날로 읽는다.
    gate = check_day_gate(as_of, borrow=borrow, sim_run_id=sim_run_id)
    if gate.gate == "BLOCKED":
        return ClosingOut(
            as_of=as_of,
            status="NOT_OPENED",
            reason=gate.reason,
            # 해석하지 않고 그대로 넘긴다. 무엇을 해야 하는지는 개장이 아는 사실이다.
            next_action=gate.next_action,
        )

    if ledger_gap:
        # 장부가 안 선 날은 닫지 않는다. 입고가 안 들어오고 채권이 안 서고 수금이
        # 안 반영된 채로 그날을 닫으면 그 틀린 숫자가 손익 곡선의 확정값으로 앉는다.
        # 판단을 막는 이유가 그대로 마감을 막는 이유다.
        #
        # 어댑터를 아예 부르지 않는다 — 재무 쪽에 반쯤 적힌 행이 남으면 안 된다.
        # `NOTHING_DUE` 로 접지 않는 이유는 모듈 설명 §2 의 어휘 구분이다.
        return ClosingOut(as_of=as_of, status="BLOCKED", reason=ledger_gap)

    absent = missing()
    if absent:
        # 미등록은 오류가 아니다. 그 파트가 하루를 닫지 않는다는 뜻이고, "그날 닫을
        # 움직임이 없다" 와 다른 사실이다 — `missing` 이 그것을 가른다. 앱 기동 시에는
        # bootstrap 이 재무 어댑터를 등록하므로, 등록소가 비어 있는 실행에서만 이 분기를
        # 탄다.
        return ClosingOut(
            as_of=as_of,
            status="NOTHING_DUE",
            reason=f"마감 미등록: {', '.join(absent)}",
            missing=list(absent),
        )

    adapters = registered()
    open_connection = core_db.connection if borrow is None else borrow
    with open_connection() as conn:
        try:
            # 파트마다 정확히 한 번 부른다. 두 번 부르고 어댑터의 멱등에 기대지 않는다.
            results = [
                adapters[part].close(conn, as_of=as_of, sim_run_id=sim_run_id) for part in PARTS
            ]
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - 마감 실패가 그날 걷기 결과를 바꾸면 안 된다.
            conn.rollback()
            return ClosingOut(as_of=as_of, status="FAILED", reason=f"마감 실패: {exc}")

    return _aggregate(as_of, results)


def _aggregate(as_of: date, parts: list[ClosingPartOut]) -> ClosingOut:
    """파트 결과를 전체 어휘로 취합한다. 값을 다시 세지 않는다.

    ```text
    BLOCKED 가 하나라도  → BLOCKED     닫을 게 있었는데 못 닫았다
    CLOSED 가 하나라도   → CLOSED
    전부 NOTHING_DUE     → NOTHING_DUE
    ```

    순서가 계약이다. `BLOCKED` 를 먼저 보는 이유는 그것이 사람이 봐야 하는 사실이기
    때문이다 — `CLOSED` 뒤로 밀면 "오늘 닫았다" 로 지나간다.

    파트 결과를 그대로 싣는다. `created` 를 `len(closed)` 로 다시 세거나 금액을
    합치지 않는다 — 세는 순간 마스터가 계산을 시작한다.
    """
    blocked = [part for part in parts if part.status == "BLOCKED"]
    if blocked:
        return ClosingOut(
            as_of=as_of,
            status="BLOCKED",
            reason=f"닫을 것이 있는데 막혔다: {', '.join(part.part for part in blocked)}",
            parts=parts,
        )
    if any(part.status == "CLOSED" for part in parts):
        return ClosingOut(as_of=as_of, status="CLOSED", parts=parts)
    return ClosingOut(as_of=as_of, status="NOTHING_DUE", parts=parts)
