"""
receivable.py — 판매 확정이 채권을 만드는 자리. 채권 등록소(`registry/receivable.py`)를 부른다.

역할: 재무 경계는 재무에 있다. `receivables` 원장에 멱등으로 넣는 것도,
`finance_states.receivables_krw` 를 같이 올리는 것도, 같은 `sale_id` 로 다른 사실이
들어오면 막는 것도 `app/finance/service/receivables.py` 의 `confirm_receivable` 안에 있다.
이 모듈은 그것을 판매 확정 뒤에 부르는 자리다. 앱이 기동할 때 `registry/bootstrap.py` 가
재무 채권 어댑터(`adapters/finance_parts.py` 의 `FinanceReceivableAdapter`)를 채권
등록소에 건다. 배선이 없으면 구현이 다 서 있어도 매일 조용히 아무 일도 일어나지 않는다
(`register_inbound` · `register_collection` 과 같은 모양).

## 판매·재무 결정 (2026-09-09)

```text
판매 확정 → receivables_krw += 원금   (issued_date = sale_date)
수금      → current_cash_krw += · receivables_krw −=

"Sales 에서 Finance 를 직접 호출하지 않는다.
 Master 가 확정 트랜잭션을 잇는 위치에서 Finance boundary 를 호출하는 것이 맞다."
```

```text
Sales     확정된 판매의 정본 — sale_date · collection_due_date · total_amount_krw
Finance   채권 원장의 정본 — receivable_id · issued_date · (sim_run_id, financing_mode) 축
Master    그날 확정분을 운반하고 호출한다.
          채권 금액도 기일도 스스로 계산하지 않는다
```

`due_date` 를 마스터가 계산하지 않는다. "sale_date + payment_days" 는 판매·재무의
계약값이다. 마스터가 그 식을 다시 쓰면 결제조건이 바뀌는 날 두 곳이 다른 답을 내고, 그
사고는 오류 없이 기일만 바꾼다. `sales.collection_due_date` 를 읽고, 그 칸이 비어 있으면
지어내지 않고 막는다.

## 하루 순서

하루의 걸음이 각자 멱등하고 각자 실패한다.

```text
① open_day(as_of)           상태 행을 보장한다        ← 먼저 (적을 자리가 있어야 한다)
② receive_arrivals(as_of)   도착분을 실제로 받는다
③ issue_receivables(as_of)  판매 확정분을 채권으로 세운다  ← 여기
④ collect_receipts(as_of)   수금 사건을 반영한다
⑤ run_procurement(as_of)    그 위에서 판단한다
```

수금보다 앞이다. 채권이 서야 수금할 것이 있다. 지금 데이터는 결제조건이 30일이라 같은
날 수금될 일이 없지만, 순서가 계약이다.

`run_procurement` 안에 넣지 않는다. 넣으면 판단 한 번이 채권 잔액을 움직이고 "같은
`as_of` 로 백번 돌려도 같은 답" 이 깨진다 — 개장·입고·수금을 판단 밖에 둔 이유와 같다.

날마다 돈다. 실행일이 아니다. `sales.sale_date` 는 판매가 정한 날이고, 마스터가 실행일
달력으로 그것을 밀면 토요일 판매의 채권이 월요일 장부에 선다(`collection.py` ·
`inbound.py` 와 같은 결). 걷기가 휴장일을 건너뛰면 그 뒤 첫 개장일에 한 번 세운다(실측
2026-03-07 · 04-04 · MISSING_RECEIVABLE 6). 채권의 `sale_date` 는 판매 값 그대로라 장부의
날짜는 밀리지 않는다 — 세우는 날만 늦다(`handled_on_first_open_day`).

## 재무 채권 파트 (`issue_finance_receivables`)

등록소에 꽂히는 재무 채권 구현(`FinanceReceivableAdapter.issue`)의 본문이다. 등록소
Protocol 표면(생성 인자 `sim_run_id` · 대역 자리 `read_axis` · `load_sales` · `confirm`)은
`adapters/finance_parts.py`, 확정 판매 SQL(`read_confirmed_sales`)은
`repository/sales_reads.py` 다. 하는 일은 셋이다.

```text
① 재무 축을 물어본다                     financing_mode 를 마스터가 고르지 않는다
② 그날 확정된 판매를 읽는다               sales 가 정본이다
③ 한 건씩 confirm_receivable 에 넘긴다     채권 원장은 재무 것이다
```

마스터 Protocol 은 `as_of` 만 나르는데 `ReceivableCreateInput` 은 여덟 칸을 요구한다. 그
여덟의 주인은 다음과 같다.

```text
sim_run_id            마스터가 정한다 — 이번 호출의 축(인자)
financing_mode        마스터가 고르지 않는다 — get_finance_runtime_axis() 로 묻는다
sale_date             sales.sale_date
issued_date           마스터가 정한다 — 그날 `as_of` 다 (sale_date 와 다를 수 있다)
customer_partner_id   sales.customer_partner_id
due_date              sales.collection_due_date 를 읽는다
original_amount_krw   sales.total_amount_krw
sale_id               sales.sale_id
```

`financing_mode` 를 상수로 박으면 안 된다. 실측으로 `finance_states` 에 `LOAN_BASELINE`
과 `BASE_NO_LOAN` 이 공존한다. 마스터가 하나를 골라 박으면 "무차입 상태가 대출 baseline
자리에 조용히 들어오는" 사고가 나고, 그 사고는 오류 없이 숫자만 바꾼다 —
`FinanceCollectionAdapter` 가 같은 문장을 적어 둔다.

`due_date` 를 계산하지 않는다. 그 칸이 비어 있으면 지어내지 않고 `BLOCKED` 로 세운다 —
채권의 기일을 마스터가 발명하면 그 값으로 수금 판정이 돌고, 틀려도 오류가 나지 않는다.

임포트 시점에 DB 를 읽지 않는다. 축 조회도 판매 조회도 `issue()` 안에서 일어난다.
배선이 DB 를 요구하기 시작하면 앱이 뜨는 조건이 조용히 늘어난다.

축의 `sim_run_id` 가 마스터 것과 다르면 막는다(fail-closed). 재무가 읽은 축이 다른
실행을 가리키는데 마스터 값으로 덮어 쓰고 진행하면 남의 실행 장부에 채권을 세운다. 그것은
오류 없이 남의 채권 잔액을 늘린다. 그래서 `BLOCKED` 다. `NOTHING_DUE` 로 접으면 "오늘은
판 게 없었다" 로 읽히고, 서 있어야 할 채권이 없는 채로 매입 판단이 돈다.

대상 판매에 `DELIVERED` 를 넣는다.

```text
WHERE 납품 처리일 = as_of   (당일, 휴장이면 그 뒤 첫 개장일 · handled_on_first_open_day)
  AND order_status IN ('CONFIRMED', 'READY', 'DELIVERED')
```

빼면 다시 걸었을 때 채권이 영영 서지 않는다. 하루 실행의 출고 단계가 같은 날 안에서
`CONFIRMED → DELIVERED` 로 바꾸므로, 두 번째 걸음에서는 그 판매가 대상 목록에서
사라진다. 첫 걸음에 채권이 안 섰으면 그 뒤로는 기회가 없다.

`outbound_flow.due_sale_items` 와 같은 목록을 쓰지 않는다. 그쪽은 "오늘 나가야 하는가"
라 `CONFIRMED · READY` 만 본다. 판정 대상이 다르다 — 한 함수로 묶으면 한쪽 규칙을 고치는
날 다른 쪽이 조용히 바뀐다. `CANCELLED` 는 넣지 않는다. 취소된 판매에 채권을 세우면 없는
돈이 매입 cap 을 늘린다.

실패 처리: `ReceivablePersistenceConflict` 는 `BLOCKED` 이고 나머지 예외는 올린다.

```text
ReceivablePersistenceConflict   재무가 "이 사실은 이렇게 못 적는다" 고 말한 것 → BLOCKED
그 밖의 예외                     무엇이 실패했는지 모른다 → 올린다 → 마스터가 롤백하고 FAILED
```

왜 가르나: 앞은 재무가 쿼리를 정상으로 마치고 낸 판정이라 트랜잭션이 살아 있고, 다음
판매를 계속 볼 수 있다. 뒤는 DB 오류일 수 있고 그때는 트랜잭션이 이미 죽어 있어 다음
문장이 전부 같은 오류로 줄줄이 실패한다 — 그것을 건별 사유로 적으면 사고 하나가 사고
여럿으로 보인다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from app.contracts.parts import ReceivablePartOut
from app.core import db as core_db
from app.finance.schemas.data_port import FinanceDataNotReady
from app.finance.schemas.finance_state import FinanceRuntimeAxis
from app.finance.schemas.receivables import ReceivablePersistenceConflict
from app.finance.schemas.sales_validation import ReceivableCreateInput
from app.master.registry.receivable import PARTS, missing, registered
from app.master.registry.sim_run_binding import bind_sim_run
from app.master.repository.sales_reads import ConfirmedSale
from app.master.schemas.receivable import ReceivableOut
from app.master.service.day_gate import check_day_gate

# ── 경계 ────────────────────────────────────────────────────────────────


def issue_receivables(
    as_of: date, *, borrow: core_db.Borrow | None = None, sim_run_id: str
) -> ReceivableOut:
    """`as_of` 에 확정된 판매를 한 트랜잭션으로 채권으로 세운다.

    `open_day` 다음이고 `collect_receipts` 앞이다. 상태 행이 있어야 채권을 적을 자리가
    있고, 채권이 서야 수금할 것이 있다. 다만 함수는 따로다 — 묶으면 실패 원인이
    뭉개진다.

    실패 처리: 예외를 밖으로 내지 않는다. `receive_arrivals` · `collect_receipts` 와 같다 —
    발행 실패가 판단을 멈추면 그날 하루가 통째로 서고, 그건 채권 하나보다 크다.

    예외 전파 여부와 후속 진행 여부는 다른 물음이다. 예외를 올리지 않는 것이 이 함수의
    계약이지만, 그렇다고 판단을 계속한다는 뜻은 아니다. 채권이 안 서면
    `receivables_krw` 가 적게 잡히고, 그것은 매입 cap 이 보는 값이다. 그래서
    `scheduler._ledger_gap` 이 이 값도 본다 — 진행 여부를 정하는 것은 부르는 쪽이고, 이
    함수는 상태를 값으로 돌려줄 뿐이다.

    달력일이다. `sales.sale_date` 가 정본이고 실행일 달력으로 밀지 않는다.

    개장 Gate 를 먼저 본다(`collect_receipts` 와 같은 이유).

      ```text
      Gate BLOCKED   →  NOT_OPENED     세울 것이 있는지조차 안 묻는다
      Gate PASS      →  평소대로
      ```

    `check_day_gate` 는 열지 않고 묻기만 한다. 여기서 `open_day` 를 부르면 발행이
    개장의 부작용이 된다. 미등록은 PASS 다(`day_gate` 계약).

    :param sim_run_id: 어느 실행의 장부인가(`#531`). 기본값이 없다. 번인 상수로 메우면
                    축을 안 준 호출이 조용히 번인 장부에 쓴다. 걷기는
                    `run_scheduled_day` 가, 라우터는 요청이 준 축을 싣는다.
    """
    gate = check_day_gate(as_of, borrow=borrow, sim_run_id=sim_run_id)
    if gate.gate == "BLOCKED":
        return ReceivableOut(
            as_of=as_of,
            status="NOT_OPENED",
            reason=gate.reason,
            # 해석하지 않고 옮긴다. 무엇을 해야 하는지는 개장이 아는 사실이다.
            next_action=gate.next_action,
        )

    absent = missing()
    if absent:
        # 미등록은 오류가 아니다. 그 파트가 채권을 세우지 않는다는 뜻이고,
        # "오늘 확정된 판매가 없다" 와 다른 사실이다 — `missing` 이 그것을 가른다.
        return ReceivableOut(
            as_of=as_of,
            status="NOTHING_DUE",
            reason=f"채권 발행 미등록: {', '.join(absent)}",
            missing=list(absent),
        )

    open_connection = core_db.connection if borrow is None else borrow
    with open_connection() as conn:
        try:
            # 등록소가 든 축이 아니라 이번 호출의 축으로 묶는다(`#531`).
            # `FinanceReceivableAdapter` 는 그 축을 재무 축과 대조해 fail-closed 한다 —
            # 등록소가 프로세스 시작 때 든 상수로 쓰면 매입 원장만 새 실행에
            # 앉고 이쪽은 번인에 남는다.
            #
            # `try` 안이다. 축이 비면 `bind_sim_run` 이 막는데, 그 실패도 예외로
            # 올라가지 않고 아래 `except` 가 `FAILED` + 사유로 옮긴다 — 채권이
            # 그날을 통째로 세우면 안 된다는 이 함수의 계약 그대로다.
            adapters = {
                part: bind_sim_run(impl, sim_run_id) for part, impl in registered().items()
            }
            results = [adapters[part].issue(conn, as_of=as_of) for part in PARTS]
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - 발행 실패가 그날을 통째로 세우면 안 된다.
            conn.rollback()
            return ReceivableOut(as_of=as_of, status="FAILED", reason=f"채권 발행 실패: {exc}")

    return _aggregate(as_of, results)


def _aggregate(as_of: date, parts: list[ReceivablePartOut]) -> ReceivableOut:
    """파트 결과를 전체 어휘로 취합한다.

    ```text
    BLOCKED 가 하나라도  → BLOCKED     세울 게 있었는데 못 세웠다
    ISSUED 가 하나라도   → ISSUED
    전부 NOTHING_DUE     → NOTHING_DUE
    ```

    순서가 계약이다. `BLOCKED` 를 먼저 보는 이유는 그것이 사람이 봐야 하는 사실이기
    때문이다 — `ISSUED` 뒤로 밀면 "오늘 세웠다" 로 지나간다.
    """
    blocked = [part for part in parts if part.status == "BLOCKED"]
    if blocked:
        return ReceivableOut(
            as_of=as_of,
            status="BLOCKED",
            reason=f"채권을 세울 것이 있는데 막혔다: {', '.join(part.part for part in blocked)}",
            parts=parts,
        )
    if any(part.status == "ISSUED" for part in parts):
        return ReceivableOut(as_of=as_of, status="ISSUED", parts=parts)
    return ReceivableOut(as_of=as_of, status="NOTHING_DUE", parts=parts)


def issue_finance_receivables(
    conn: Any,
    *,
    as_of: date,
    sim_run_id: str,
    read_axis: Callable[..., FinanceRuntimeAxis],
    load_sales: Callable[..., tuple[ConfirmedSale, ...]],
    confirm: Callable[..., Any],
) -> ReceivablePartOut:
    """재무 축을 읽고 그날 확정 판매를 한 건씩 `confirm` 에 넘긴다(마스터 채권 단계의 재무 파트).

    받은 연결(마스터 채권 단계의 연결)로 판매를 읽고 채권을 세운다 — 빌리지도 commit 하지도
    않는다. 값의 주인과 실패 처리는 모듈 머리말 「재무 채권 파트」 절에 있다.
    """
    try:
        axis = read_axis(sim_run_id=sim_run_id)
    except (FinanceDataNotReady, LookupError, ValueError) as exc:
        # 사유를 그대로 옮긴다. `finance_runtime_axis_ambiguous` 가 여기서
        # 사라지면 "막혔다" 만 남고 무엇이 모호했는지가 없어진다.
        return ReceivablePartOut(
            part="finance",
            status="BLOCKED",
            reason=f"재무 축을 읽지 못했다: {exc}",
        )

    if axis["sim_run_id"] != sim_run_id:
        # 덮어 쓰지 않는다. 남의 실행 장부에 조용히 채권을 세우는 자리다.
        return ReceivablePartOut(
            part="finance",
            status="BLOCKED",
            reason=(
                "실행 축이 다르다: 마스터 sim_run_id="
                f"{sim_run_id!r}, 재무 축 sim_run_id={axis['sim_run_id']!r}"
            ),
        )

    try:
        sales = load_sales(conn, as_of=as_of, sim_run_id=sim_run_id)
    except Exception as exc:  # noqa: BLE001 - 조회 실패를 `()` 로 접지 않는다.
        # 없는 것과 못 읽은 것은 다르다. `()` 로 접으면 표를 못 읽은 날이
        # "오늘은 판 게 없었다" 로 읽힌다.
        return ReceivablePartOut(
            part="finance",
            status="BLOCKED",
            reason=f"확정 판매를 읽지 못했다: {type(exc).__name__}: {exc}",
        )

    if not sales:
        # `NOTHING_DUE` 다. 확인했고 그날 확정된 판매가 없었다.
        return ReceivablePartOut(part="finance", status="NOTHING_DUE")

    issued: list[str] = []
    created = 0
    blocked: list[str] = []
    for sale in sales:
        if sale.collection_due_date is None:
            # 지어내지 않는다. 기일은 판매·재무의 계약값이다.
            blocked.append(f"{sale.sale_id}: collection_due_date 가 비어 있다")
            continue
        request = ReceivableCreateInput(
            sale_id=sale.sale_id,
            # 마스터가 정한다.
            sim_run_id=sim_run_id,
            # 고르지 않는다. 재무가 읽은 값 그대로다.
            financing_mode=axis["financing_mode"],
            sale_date=sale.sale_date,
            # 발행일은 판매일이 아니라 그날이다. 휴장일 판매는 그 뒤 첫
            # 개장일에 발행되므로 `sale_date` 보다 늦다. 재무는 이 날짜의 상태에
            # AR 을 올리므로, 여기에 `sale.sale_date` 를 실으면 이미 지나간 날의
            # 잔액이 뒤늦게 커진다.
            issued_date=as_of,
            customer_partner_id=sale.customer_partner_id,
            # 판매가 정한 기일 그대로다.
            due_date=sale.collection_due_date,
            original_amount_krw=sale.total_amount_krw,
        )
        try:
            result = confirm(conn, request)
        except ReceivablePersistenceConflict as exc:
            # 재무가 정상으로 마치고 낸 판정이다 — 트랜잭션이 살아 있으므로
            # 다음 판매를 계속 본다.
            blocked.append(f"{sale.sale_id}: {exc}")
            continue
        issued.append(result.receivable_id)
        # 멱등이라 0 일 수 있다. 0 은 "이미 서 있었다" 이고 실패가 아니다.
        created += int(result.receivables_written)

    if blocked:
        # 하나라도 못 세웠으면 `BLOCKED` 다. 나머지가 섰다고 지나가면
        # `receivables_krw` 가 적게 잡힌 채로 매입 판단이 돈다.
        return ReceivablePartOut(
            part="finance",
            status="BLOCKED",
            reason="채권을 못 세운 판매가 있다: " + " · ".join(blocked),
            issued=issued,
            created=created,
        )
    return ReceivablePartOut(
        part="finance",
        status="ISSUED",
        issued=issued,
        created=created,
    )
