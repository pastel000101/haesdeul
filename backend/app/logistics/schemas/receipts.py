"""도착 Receipt 의 상태 어휘 · 결과 · 실패 종류.

★ 2026-09-30 재구성 BL-015: `logistics/receipts.py` 에서 옮겼다. 순서는 `service/receipts.py`, 열쇠
  · 행 해석은
  `domain/receipts.py`, SQL 은 `repository/receipts.py`, 도착 잠금은 `repository/locks.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

#: 조회가 낸 답. **둘 다 정상이다** — 실패가 아니다.
#:
#: ```text
#: NEW             이 입고에 Receipt 행이 아직 없다. 뒤 단계가 만들 수 있다
#: ALREADY_EXISTS  이 입고에 Receipt 행이 **이미 존재한다**
#: ```
#:
#: 🔴 **`ALREADY_EXISTS` 는 "입고 처리가 끝났다" 가 아니다.** Receipt 행 하나가
#:    있다는 사실일 뿐이고, 검수·Lot·원장 IN 이 아직 없을 수 있다. 그래서 이 축과
#:    별개로 `receipt_status` 를 함께 돌려준다 (`ReceiptExistence` 참조).
ReceiptExistenceStatus = Literal["NEW", "ALREADY_EXISTS"]

#: `inbound_receipts.receipt_status` 의 **DB CHECK 어휘 그대로다.**
#:
#: ```sql
#: ck_inbound_receipts_status
#:   CHECK (receipt_status IN ('ARRIVED','INSPECTING','INSPECTED','PUTAWAY_DONE','CLOSED'))
#: ```
#:
#: 🔴 **새 상태를 만들지 않는다.** 여기 없는 값이 DB 에서 나오면 그것은 우리가 모르는
#:    상태이고, 아는 값으로 바꿔 읽으면 그 순간 모르는 것을 아는 척하게 된다.
ReceiptStatus = Literal["ARRIVED", "INSPECTING", "INSPECTED", "PUTAWAY_DONE", "CLOSED"]

#: 값은 타입에서 **파생한다** — 어휘가 한 번만 적히게 하려는 것이다
#: (`schemas.POLICY_VERSION` 이 같은 이유로 `get_args` 를 쓴다). 둘을 나란히 적으면
#: 어휘가 바뀌는 날 두 줄을 함께 고쳐야 한다.
RECEIPT_STATUSES: frozenset[str] = frozenset(get_args(ReceiptStatus))


#: 🔴 **도착 시점의 유일한 상태다.** `ck_inbound_receipts_status` 어휘의 첫 값이고,
#: 뒤 넷은 검수·적치 단계의 값이라 여기서 쓰면 **하지 않은 일을 적는 것**이 된다.
STATUS_ON_ARRIVAL: ReceiptStatus = "ARRIVED"

#: 🔴 **창고에서 사람이 확인한 것이 아니다.** `ck_inbound_receipts_fact_source` 는
#: `HUMAN_RECORDED · SCENARIO_SIMULATED` 둘뿐이고, 자동 시뮬레이션 처리에
#: `HUMAN_RECORDED` 를 쓰면 **거짓을 적는 것**이다.
#:
#: ★ 이 값이 *"`arrived_at` 은 관측된 물리 도착이 아니라 계획된 예정일"* 이라는
#:   사실을 이미 기록한다 — 그래서 그 설명을 `note` 에 또 적지 않는다.
RECEIPT_FACT_SOURCE = "SCENARIO_SIMULATED"


class ReceiptLookupError(RuntimeError):
    """이 모듈이 내는 실패의 조상.

    ★ `ledger.InventoryLedgerError` 와 같은 결이다 — 호출자가 *"입고 Receipt 처리가
      실패했다"* 를 한 번에 잡을 수 있게 두되, 종류는 아래에서 갈라 둔다.

    ⚠️ 이름이 `Lookup` 인 것은 이 파일이 조회만 하던 시절(3-B4-C)의 흔적이다.
       3-B4-G 부터 쓰기 실패도 이 아래로 온다 — 이름을 바꾸면 이미 이 이름을 잡는
       코드가 조용히 안 잡히게 되므로 **그대로 둔다.**
    """


class InvalidInboundIdentity(ReceiptLookupError, ValueError):
    """조회 열쇠로 쓸 수 없는 `inbound_id` 다.

    🔴 **없는 열쇠로 DB 에 묻지 않는다.** 물으면 0건이 나오고, 그 0건은
       *"아직 Receipt 가 없다"* 로 읽힌다 — **없는 것과 물어보지 못한 것이 같은
       답으로 뭉개진다.**

    ★ 도착 후보 선택(`arrival.select_due_inbound`)이 이미 같은 눈으로 걸러 준다
      (`ARRIVAL_INBOUND_ID_MISSING`). 여기서 다시 보는 것은 **이 함수가 그 경로
      밖에서도 안전해야** 하기 때문이지, 저쪽을 못 믿어서가 아니다.
    """


class ReceiptIntegrityError(ReceiptLookupError, ValueError):
    """같은 `(sim_run_id, inbound_id)` 에 Receipt 가 둘 이상이다. 무결성 위반이다.

    🔴 **어느 것이 진짜인지 여기서 고르지 않는다.** 최신을 고르는 것도, 첫 행을
       고르는 것도, 조용히 하나로 합치는 것도 **전부 고르는 것**이다. 고른 뒤에는
       버려진 쪽이 있었다는 사실조차 남지 않는다.

    ★ `readmodel/current.get_active_logistics_runtime_fixture` 가 활성 fixture 2건에,
      `transition._index_by_inbound_id` 가 중복 `inbound_id` 에 하는 일과 같다 —
      깨진 상태 위에서 계속 걷지 않는다.

    ⚠️ `uq_inbound_receipts_inbound_id` 가 있으면 원래 못 생기는 상태다. 그래도
       검사하는 이유는 **제약이 아직 안 적용된 DB 도 있을 수 있어서**다 (그 표는
       `30_logistics_wms_schema.sql` 로 최근에 회수됐다).
    """


class ReceiptRowUnreadable(ReceiptLookupError, ValueError):
    """Receipt 행은 있는데 그 값을 계약대로 읽을 수 없다. 무결성 위반이다.

    🔴 **모르는 상태를 아는 값으로 바꿔 읽지 않는다.** DB CHECK 밖의
       `receipt_status` 가 나왔다면 그 행은 우리가 모르는 상태이고,
       `ARRIVED` 로 대신 읽으면 **아직 안 한 일을 안 했다고 다시 적게 된다** —
       검수·Lot 이 이미 있는 행을 처음부터 다시 도는 자리가 정확히 거기다.

    ⚠️ `ck_inbound_receipts_status` 가 있으면 원래 못 생기는 상태다. 그래도 검사하는
       이유는 **제약이 아직 안 적용된 DB 도 있을 수 있어서**이고,
       `ReceiptIntegrityError` 가 중복에 대해 같은 이유로 존재하는 것과 같다.
    """


class ReceiptFactsMissing(ReceiptLookupError, ValueError):
    """Receipt 를 쓰려는데 **권위 있는 매입 사실이 비어 있다.**

    🔴 **`purchase_item_id=NULL` 로 Receipt 를 만들지 않는다.** 그러면 다음 실행이
       `ALREADY_EXISTS` 를 보고 **그 입고를 영영 건너뛴다** — Receipt 만 남고 재고가
       안 들어온 채 고착되고, 나중에 매입 참조가 생겨도 그 행을 보강할 경로가
       저장소에 없다 (3-B4-D 감사 F 항목).

    ★ 상류가 이미 막는다 — `arrival.select_due_inbound` 이 참조 없는 행을
      `ARRIVAL_PURCHASE_REFERENCE_MISSING` 으로 `blocked` 에 둔다. 여기서 다시 보는
      것은 **이 함수가 그 경로 밖에서도 안전해야** 하기 때문이다.
    """


@dataclass(frozen=True)
class ReceiptExistence:
    """조회 결과. **작게 둔다.**

    ★ `arrived_at` · 수량들 · `fact_source` 는 싣지 않는다 — 지금 쓰지 않는 값이고,
      실어 두면 뒤 단계가 **여기서 읽은 낡은 값**을 쓰게 된다.

    🔴 **`receipt_status` 는 예외다. 이것은 실어야 한다.**
       `ALREADY_EXISTS` 만으로는 *"이 입고를 더 볼 필요가 없다"* 와 구별이 안 된다 —

    ```text
    Receipt 가 ARRIVED 로 있다
    검수 없음 · Lot 없음 · 원장 IN 없음
    ⇒ 행은 있지만 **입고 처리는 끝나지 않았다**
    ```

       그 상태로 건너뛰면 Receipt 만 남고 재고가 안 들어온 채 영구 고착된다.
       뒤 단계가 그것을 가르려면 **지금 그 사실이 나가 있어야** 한다.

    ⚠️ **여기서 진행 여부를 정하지는 않는다.** 어느 상태에서 무엇으로 이어갈지는
       별도의 상태기계 감사가 정한다 — 이 판은 *"실제 상태가 무엇인가"* 만 답한다.
    """

    status: ReceiptExistenceStatus
    #: `ALREADY_EXISTS` 면 그 행의 권위 있는 `receipt_id`, `NEW` 면 `None`.
    receipt_id: str | None
    #: `ALREADY_EXISTS` 면 DB 에 적힌 `receipt_status` 그대로, `NEW` 면 `None`.
    #:
    #: 🔴 **기본값을 두지 않는다.** `NEW` 일 때 `"ARRIVED"` 를 넣으면 *"아직 없다"* 와
    #:    *"막 도착했다"* 가 같은 값이 된다.
    receipt_status: ReceiptStatus | None


@dataclass(frozen=True)
class ReceiptWriteResult:
    """`create_arrived_receipt` 의 결과. **작게 둔다.**

    🔴 **`applied=False` 는 "입고 처리가 끝났다" 가 아니다.** 뜻은 하나뿐이다 —
       *"이 호출이 새 Receipt 를 만들지 않았다. 행이 이미 있었기 때문이다."*
       그 행이 `ARRIVED` 인데 검수·Lot·원장 IN 이 없을 수 있고, 그때 건너뛰면
       재고가 안 들어온 채 고착된다.

    ★ `ledger.LedgerResult.applied` 와 같은 뜻이다 — 멱등 재실행에서 *"이번에 실제로
      바꿨나"* 를 답하는 축이지, *"할 일이 남았나"* 가 아니다.
    """

    #: 이번 호출이 Receipt 행을 **새로 만들었나.**
    applied: bool
    #: 새로 만들었으면 그 id, 이미 있었으면 **DB 에 적힌 권위 있는 id.**
    receipt_id: str
    #: 새로 만들었으면 `ARRIVED`, 이미 있었으면 **그 행의 현재 상태 그대로.**
    #:
    #: 🔴 **상태를 진행시키지 않는다.** 이 함수는 `ARRIVED` 를 만들 뿐이고, 어느
    #:    상태에서 무엇으로 이어갈지는 별도 상태기계 감사가 정한다.
    receipt_status: ReceiptStatus
