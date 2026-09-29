"""자동 유지보수의 폐기 ID 규칙 · 입력 검사.

★ 2026-09-30 재구성 BL-015: `logistics/auto_maintenance.py` 에서 옮겼다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.logistics.schemas.maintenance import InvalidAutoMaintenanceRequest


def auto_disposal_id_for(*, sim_run_id: str, lot_id: str, as_of: date) -> str:
    """자동 폐기 건의 정체성. **순수 계산이고 결정론이다.**

    ```text
    AUTO-{sim_run_id}-{lot_id}-{YYYYMMDD}
    → disposal_move_id_for 가 MOVE-DISPOSE-… 를 얹는다
    ```

    🔴 난수도 벽시계도 시퀀스도 쓰지 않는다. 같은 실행·같은 Lot·같은 날이면 언제
       불러도 같은 값이다.

    ★ **`as_of` 를 넣는다.** `confirm_disposal` 의 멱등 판정은 `moved_at` 까지 대조하고
      이 사이클은 `moved_at = as_of` 로 적는다 — 날짜를 ID 에서 빼면 **ID 는 같은데
      사실이 다른** 조합이 만들어질 수 있고, 그때 재실행이 `DisposalIntegrityError`
      로 영구히 막힌다. ID 와 사실이 같은 축으로 움직이게 둔다.

    ⚠️ **멱등의 본체는 이 ID 가 아니라 잔량이다.** 전량 폐기가 커밋되면 그 Lot 은
       `load_lot_turnover`(잔량 > 0 만 본다)에서 사라져 다음 사이클이 아예 안 집는다.
       이 ID 는 **같은 트랜잭션 안에서 두 번 불렸을 때**를 닫는다.
    """
    maintenance_text(sim_run_id, 칸="sim_run_id")
    maintenance_text(lot_id, 칸="lot_id")
    return f"AUTO-{sim_run_id}-{lot_id}-{as_of:%Y%m%d}"


def maintenance_text(값: Any, *, 칸: str) -> str:
    if not isinstance(값, str) or not 값.strip():
        raise InvalidAutoMaintenanceRequest(f"자동 유지보수에 쓸 수 없는 {칸} 다: {값!r}")
    return 값
