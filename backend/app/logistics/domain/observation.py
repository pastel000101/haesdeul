"""관측일 도우미 — Lot 수량 · 상태가 «언제의 값인가».

★ 2026-09-30 재구성 BL-015: `logistics/monitoring/observe.py` 의 관측일 도우미 둘을 옮겼다. DB 를
  만지지 않는다.
  스냅샷 · 정책 관측일 규칙은 모델이 쓰므로 `schemas/monitoring.py` 에 있다.
"""

from __future__ import annotations

from datetime import date

from app.logistics.schemas.historical import LedgerLotState
from app.logistics.schemas.monitoring import LEDGER_MOVE_UNRESOLVED, OBSERVATION_INCONSISTENT
from app.logistics.schemas.snapshot import InventoryLotSnapshot
from app.logistics.schemas.vocabulary import ACTIVE_LOT_STATUS, DISPOSED_LOT_STATUS


def quantity_observed_as_of(
    ledger_state: dict[str, LedgerLotState] | None, *, lot: InventoryLotSnapshot
) -> tuple[date | None, list[str]]:
    """이 Lot 의 잔량을 **언제부터 알 수 있었나** = 마지막 원장 이동일.

    ```text
    원장을 못 읽었다           None            (사유는 이미 적혔다)
    그 Lot 의 이동이 없다      None + 사유      production 경로면 날 수 없는 일이다
    캐시 ≠ 원장 누계           None + 사유      갈린 값의 «언제부터» 는 못 댄다
    그 밖                      max(moved_at)
    ```

    ★ **`remaining_qty_kg` 는 파생 캐시다** (`repository` 가 적어 둔 경고). 정본은
      `inventory_moves` 이고, 둘이 갈리면 그날의 모든 판정이 조용히 틀린다 — 그래서
      문제를 열기 전에 한 번 맞대어 보고, 다르면 **사실만 적고 날짜는 비운다.**

    🔴 **탐지를 막지는 않는다.** 날짜가 없다고 문제를 안 여는 것이 아니다 — 문제는
       열되 *"이 근거의 관측일은 모른다"* 를 그대로 남긴다.
    """
    if ledger_state is None:
        return None, []
    ledger_row = ledger_state.get(lot.lot_id)
    if ledger_row is None:
        return None, [f"{LEDGER_MOVE_UNRESOLVED}:{lot.lot_id}"]
    if ledger_row.balance_kg != lot.available_qty_kg:
        return None, [f"{OBSERVATION_INCONSISTENT}:{lot.lot_id}"]
    return ledger_row.last_moved_at, []


def status_observed_as_of(
    status: str, *, received_at: date | None, last_moved_at: date | None
) -> date | None:
    """이 Lot 의 **상태**를 언제부터 알 수 있었나. 어휘마다 근거가 다르다.

    ```text
    ACTIVE    received_at     Lot INSERT 가 적는 값이다 (inbound_stock._insert_lot).
                              🔴 되돌리는 writer 가 없다 — 원장은 상태를 안 건드리고
                                 (ledger._update_remaining), 잔량이 0 이 돼도 그대로다
    DISPOSED  마지막 이동일    잔량을 0 으로 만든 DISPOSE 의 날
                              (disposal._mark_disposed 가 같은 판에서 적는다)
    그 밖      None            DEPLETED · HOLD 는 production writer 가 하나도 없다
    ```

    🔴 **없는 상태를 날짜로 메우지 않는다.** 쓰는 코드가 없는 어휘는 되살릴 사건도
       없다 — `schemas/historical.HistoricalLotState` 가 `HOLD` 를 빼는 것과 같은 판단이다.
    """
    if status == ACTIVE_LOT_STATUS:
        return received_at
    if status == DISPOSED_LOT_STATUS:
        return last_moved_at
    return None
