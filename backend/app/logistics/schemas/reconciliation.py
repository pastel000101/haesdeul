"""고착 입고 일정 정리의 결과 · 실패 종류.

★ 2026-09-30 재구성 BL-015: `logistics/inbound_reconciliation.py` 에서 옮겼다. 순서는
  `service/reconciliation.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


class InboundReconciliationError(RuntimeError):
    """이 모듈이 내는 오류의 뿌리."""


class InvalidReconciliationRequest(InboundReconciliationError, ValueError):
    """요청 자체가 성립하지 않는다. **DB 에 묻기 전에 막는다.**

    🔴 **빈 축으로 물으면 0건이 돌아오고, 그 0건이 *"이미 걷혔다"* 로 읽힌다.**
       없는 것과 물어보지 못한 것은 다른 사실이다
       (`inbound_stock.InvalidReceivingAxis` 와 같은 판단).

    🔴 **`reason` · `source_ref` 도 같은 등급으로 막는다.** 근거 없이 일정을 걷는 것은
       근거 없이 지우는 것과 같다 — 이 함수는 사람의 판단을 실행하는 자리이지 스스로
       판단하는 자리가 아니라서, 그 판단이 무엇이었는지를 빈칸으로 둘 수 없다.
    """


class ScheduleAlreadyMaterialized(InboundReconciliationError, ValueError):
    """그 `inbound_id` 에 **이미 입고 계보가 붙어 있다.** orphan 이 아니다.

    ```text
    Receipt 있음                도착 처리가 시작됐다 — materialize 의 몫이다
    Receipt + Lot 있음          가용재고가 됐다
    Receipt + Lot + IN Move 있음  원장까지 나갔다
    ```

    ⚠️ **셋 중 어느 것이든 일정만 걷으면 안 된다.** 일정이 사라지면 그 재고가 어디서
       왔는지 되짚을 자리가 없어지고, 남은 것은 출처 없는 Lot 이다.
    """


class InboundLineageAmbiguous(InboundReconciliationError, ValueError):
    """같은 `inbound_id` 에 Receipt 가 **둘 이상**이다. 어느 것도 고르지 않는다.

    🔴 **첫 행을 임의로 집지 않는다.** 둘 중 무엇이 진짜인지는 데이터가 말해 주지
       않고, 골라 버리면 나머지 하나가 조용히 없는 것이 된다.

    ⚠️ **정상 경로로는 설 수 없는 상태다** — `uq_inbound_receipts_inbound_id`
       (`sim_run_id` + `inbound_id`) 가 막는다. 그래도 여기서 세는 이유는 이 함수가
       *"지워도 되나"* 를 묻는 자리이기 때문이다. 제약이 빠진 판이나 손으로 넣은
       행에서 이 상태가 서면, 모르는 채 지우는 것보다 멈추는 것이 맞다.
    """


@dataclass(frozen=True)
class MaterializedInbound:
    """거부 근거로 함께 싣는 **계보 한 줄.** 값이고 아무것도 안 쓴다."""

    receipt_id: str
    receipt_status: str
    #: 그 Receipt 에서 나온 Lot. 아직 없으면 `None` (도착만 하고 재고화 전).
    lot_id: str | None
    #: 그 Lot 의 원장 `IN`. 아직 없으면 `None`.
    move_id: str | None


@dataclass(frozen=True)
class InboundReconciliationResult:
    """정리 1회의 결과. **터진 것은 예외로 나가고, 여기 오는 것은 다 정상이다.**

    ```text
    applied=True  · removed=1   이번 호출이 그 일정을 닫았다
    applied=False · removed=0   이미 닫혀 있었다 — 재실행의 정상 경로다
    ```

    🔴 **`removed` 는 닫은 *일정 건수* 다.** 한 번에 한 건만 지목하므로 0 아니면 1 이고,
       같은 열쇠가 둘일 수 없는 것은 `inbound_schedules` PK 가 보장한다.

    ★ **`source_ref` 는 fixture 행에 실제로 적힌다** (`logistics_runtime_fixture.
      source_ref`). 걷어낸 뒤 그 행의 근거는 *"누가 왜 이 목록을 이렇게 만들었나"* 이고,
      그것이 바로 이번 정리이기 때문이다.

    ⚠️ **`reason` 은 DB 에 안 적는다.** 담을 칸이 없고(`note` 는 하루 넘김이 쓰는 남의
       칸이다), 칸을 만드는 것은 `database/` 의 일이다 — 없는 자리에 억지로 끼워 넣지
       않는다. 호출자가 자기 감사 기록에 남긴다.

    ★ **걷어낸 사실 셋을 함께 싣는다.** 지운 뒤에는 fixture 어디에도 안 남으므로,
      *"무엇을 지웠나"* 를 답할 수 있는 곳이 이 결과뿐이다. 재실행 no-op 이면 지운 것이
      없어 셋 다 `None` 이다.
    """

    applied: bool
    inbound_id: str
    sim_run_id: str
    as_of: date
    usage_scope: str
    removed: int
    #: 사람이 적은 정리 사유. 빈 값은 애초에 못 들어온다. **DB 에는 안 적힌다.**
    reason: str
    #: 그 판단의 근거 참조 (티켓 · 감사 문서 등). 역시 빈 값을 안 받는다.
    source_ref: str
    #: 걷어낸 일정의 품목. 재실행 no-op 이면 `None`.
    item: str | None = None
    #: 걷어낸 일정의 수량. 재실행 no-op 이면 `None`.
    quantity_kg: Decimal | None = None
    #: 걷어낸 일정의 도착 예정일. 재실행 no-op 이면 `None`.
    expected_arrival_date: date | None = None
