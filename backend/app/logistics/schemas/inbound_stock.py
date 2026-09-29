"""재고화(검수 끝난 Receipt → Lot · 원장 IN)의 결과 · 실패 종류와 입고 사유 어휘.

★ 2026-09-30 재구성 BL-015: `logistics/inbound_stock.py` 에서 옮겼다. 순서는
  `service/inbound_stock.py`, 대조 규칙은
  `domain/inbound_stock.py`, SQL 은 `repository/inbound_stock.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.logistics.schemas.receipts import ReceiptStatus

#: 🔴 원장에 이미 있는 어휘다 (실측: `IN` 75행이 이 사유를 쓴다). 새 사유를 만들지 않는다.
IN_REASON_CODE = "PURCHASE_RECEIPT"


class InboundStockError(RuntimeError):
    """이 모듈이 내는 실패의 조상."""


class LotIntegrityError(InboundStockError, ValueError):
    """Lot 과 Receipt 가 서로를 배반한다. **조용히 고치지 않는다.**"""


class LotConflict(InboundStockError, ValueError):
    """같은 Receipt 에 **다른 사실**의 Lot 이 이미 있다.

    🔴 덮지도 버리지도 않는다 — 그 Lot 의 원가·등급으로 이미 판매·평가가 돌았을 수
       있어, 갈아 끼우면 그 계산들이 소리 없이 근거를 잃는다.
    """


class InvalidReceivingAxis(InboundStockError, ValueError):
    """일정을 읽을 **조회 축**의 어느 칸이 비어 있다.

    ```text
    uq_log_runtime_fixture (sim_run_id, as_of, usage_scope)
                            ↑                   ↑
                            이 둘이 비면 여기서 멈춘다
    ```

    🔴 **없는 열쇠로 묻지 않는다.** 빈 값으로 fixture 를 조회하면 0건이 돌아오고, 그
       0건은 *"그날 행이 없다"* 로 읽힌다 — **없는 것과 물어보지 못한 것이 같은
       답으로 뭉개진다** (`day_open.LogisticsDayOpening.__init__` ·
       `receipts.InvalidInboundIdentity` 와 같은 규율).

    ★ **`as_of` 는 여기서 안 본다.** 타입이 `date` 라 빈 값이 될 수 없다 — 빈 문자열이
      들어올 수 있는 두 칸만 막는다.

    ⚠️ **`None` 으로 접어 "모든 실행" · "모든 범위" 를 뜻하게 하지 않는다.** 도착
       처리는 남의 실행 장부를 건드리면 안 되는 쓰기 경로다.
    """


class ScheduleIntegrityError(InboundStockError, ValueError):
    """도착 처리를 걸 자리가 없거나, 그 Receipt 가 일정으로 되짚어지지 않는다.

    ```text
    그날 fixture 행이 없다            lock_fixture_row_for_receiving  도착 처리를 걸 Header 가 없다
    Receipt 에 inbound_id 가 없다   materialize…                    일정으로 되짚을 열쇠가 없다
    ```

    🔴 **`inbound_id` 없는 Receipt 를 그냥 넘기지 않는다.** 그 값이 `inbound_schedules`
       와 잇는 유일한 열쇠라(`load_schedule_views` 의 계보 조인), 없으면 그 일정이
       **영원히 «아직 안 들어온 것»** 으로 남아 도착 대상과 Capacity 에 계속 선다.

    ⚠️ **이름의 «일정»은 이제 `inbound_schedules` 를 가리킨다.** 종전에는 fixture 의
       두 JSON 칸을 뜻했고 B-1(두 칸 대조) 위반이 이 예외의 자리였다 — 그 칸은
       Runtime 에서 죽었다(W3-3).
    """


@dataclass(frozen=True)
class InboundStockResult:
    """`materialize_inspected_inbound` 의 결과. **작게 둔다.**

    🔴 **`applied=False` 는 "할 일이 없었다" 가 아니라 "이번 호출이 새로 만든 것이
       없다" 다.** 재실행이 정상적으로 여기로 온다.
    """

    #: 이번 호출이 Lot 이나 Move 를 **새로 만들었나.**
    applied: bool
    receipt_status: ReceiptStatus
    #: `accepted = 0` 이면 `None` — 만들 재고가 없었다는 뜻이다.
    lot_id: str | None
    move_id: str | None
    accepted_qty_kg: Decimal
