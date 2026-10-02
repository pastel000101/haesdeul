"""maintenance.py — 그날 자리를 비우는 단계를 마스터가 부른다.

```text
개장 → 물류 유지보수 → 미적용 전이 재시도 → 입고 → 채권 → 수금 → [장부 관문] → …
```

이 단계가 있는 이유: 창고에서 재고가 나갈 길은 판매와 폐기다. 신선도가 지난 Lot 은
물류가 판매 가능 수량에서 빼므로(규칙대로다) 폐기로만 나갈 수 있는데, 하루 실행이
폐기를 부르지 않으면 Lot 이 ACTIVE 로 남아 창고 자리를 계속 차지한다. 실측
(2026-09-11 · 보수안 1~3월 걷기 71일): 매입은 2026-01-05 ~ 01-09 닷새 동안 15건뿐이었고,
보류 156건의 사유가 "하드 제약(창고)으로 수량이 0까지 축소되어 제안 불가" 였다(그날
`used_capacity_kg` 8,338 > `guaranteed_capacity_kg` 8,000 · `warehouse_free_kg` 0).
폐기와 자리 반환 자체는 물류(`app/logistics/service/maintenance.py` 의
`run_logistics_auto_maintenance`)에 있고, 이 파일은 그것을 부르는 자리다.

물류의 안전 조건을 여기서 다시 적지 않는다. 무엇을 자동으로 버리고 무엇을 사람에게
남기는지는 물류가 정한다("한 kg 이라도 잡혀 있으면 통째로 건너뛴다 · 자동 부분 폐기
없음"). 이 파일은 트랜잭션과 어휘만 진다. 후보를 고르는 줄도 폐기량을 정하는 줄도
여기 없다.

트랜잭션의 주인이 이 파일이다. `run_logistics_auto_maintenance` 는 커밋도 롤백도 하지
않는다 — 그 약속의 반대편이 여기다. 한 사이클이 한 커밋이고, Lot 마다 커밋하지 않는다.
잠금 순서: 물류가 배치 시작에서 출고 전역 잠금을 잡아 잠금 순서를 `3 → 1 → 4` 하나로
고정해 두는데, 중간에 커밋하면 그 잠금이 풀려 다른 실행과 역전될 자리가 생긴다.

실패 처리: 터져도 하루는 계속 간다. 예외를 값으로 옮긴다(`_stage` · `_retry_pending`
과 같은 태도). 자리를 못 비운 것과 하루를 못 산 것은 다른 사실이다.

## 사유 · 행위자 · 시각 — 마스터가 정해서 넘긴다

물류는 셋 다 기본값을 두지 않는다. "물류가 지어내지 않는다. 기본값을 두면 묻지도
않은 사유와 행위자가 장부에 사실로 선다" — 그 빈칸을 채우는 것이 부르는 쪽의 일이다.

```text
reason_code   FRESHNESS_EXPIRED    왜 버리나
recorded_by   AUTO-MAINTENANCE     누가 했나 · 사람 이름을 쓰지 않는다
occurred_at   걷기의 시간축         벽시계를 여기서 읽지 않는다
```
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from datetime import date, datetime

from app.core import db as core_db
from app.logistics.schemas.maintenance import AutoMaintenanceResult
from app.logistics.service.maintenance import run_logistics_auto_maintenance
from app.master.schemas.maintenance import MaintenanceOut

FRESHNESS_EXPIRED = "FRESHNESS_EXPIRED"
"""자동 폐기의 `reason_code`. 왜 버리는가.

새 판정 기준을 만든 이름이 아니다. 물류가 후보로 올리는 조건이 딱 하나이고
(`logistics/domain/turnover.py` 의 `is_disposal_candidate` — `remaining_freshness_days <= 0`),
이 낱말은 그 조건을 그대로 부르는 말이다. 사유에 다른 뜻을 적으면 장부가 "왜 버렸나"
에 후보 조건과 다른 답을 하게 된다.

축 이름을 새로 짓지 않는다. `contracts/core.py` 의 `BindingConstraint` 가 이미
`FRESHNESS` 를 쓰고 물류 신호도 `INVENTORY_FRESHNESS_PRESSURE` 다 — 같은 축의 말이라
읽는 사람이 어느 사실을 가리키는지 바로 안다.

주의: 뜻을 좁게 읽어야 한다. "과학적으로 부패했다" 가 아니라 `turnover.py` 가 적어 둔
그대로 "현재 계약상 판매 대상에서 빠져 폐기 검토가 필요하다" 다.

`MVP_DEMO_FIXTURE_CORRECTION` 을 쓰지 않는다. 물류가 씨앗 보정용으로 정한 사유다 —
업무 폐기를 그 사유로 적으면 나중에 둘을 가를 수 없다.
"""

AUTO_MAINTENANCE = "AUTO-MAINTENANCE"
"""자동 유지보수가 적는 `recorded_by`. `pallet_events` 에 남는 행위자다.

사람 이름을 쓰지 않는다. `AUTO-BACKFILL` 과 같은 결이고 같은 이유다 — 사람이 누르지
않았는데 눌렀다고 기록되면 그 행은 되돌릴 수 없다. 폐기는 승인보다 더하다: 물류
규칙상 "되돌릴 경로가 없다(`ADJUST_IN` 없음 · 실사 제외)".

`AUTO-BACKFILL` 을 재사용하지 않는다. 그 낱말의 뜻은 "승인을 자동으로 채웠다" 이고,
이쪽은 "창고를 자동으로 정리했다" 다 — 한 낱말로 적으면 `pallet_events` 만 보고는
어느 자동화가 한 일인지 가를 수 없다.
"""


def run_auto_maintenance(
    as_of: date,
    *,
    sim_run_id: str,
    occurred_at: datetime,
    borrow: core_db.Borrow | None = None,
    maintain_fn: Callable[..., AutoMaintenanceResult] = run_logistics_auto_maintenance,
) -> MaintenanceOut:
    """그날 자리를 비운다. 예외를 밖으로 내지 않는다.

    `receive_arrivals` · `ship_due_sales` 와 같은 모양이다 — `as_of` 를 받고, 상태를
    값으로 돌려주고, 하루의 진행을 자기 성공에 걸지 않는다.

    무엇을 버릴지 여기서 고르지 않는다. 후보도 안전 조건도 `maintain_fn` 의 것이다 —
    이 함수가 지는 것은 트랜잭션과 어휘 셋뿐이다.

    커밋이 여기 있다. 물류가 하지 않는 그 일이다. 실패하면 롤백하고 `FAILED` 로
    돌아선다 — 반쯤 비운 창고를 장부에 남기지 않는다.

    :param sim_run_id: 어느 실행의 창고인가. 이번 실행 축으로만 돈다.
    :param occurred_at: `pallet_events` 에 남을 시각. 인자다 — 이 함수는 시계를 읽지
        않는다. 걷기의 시간축을 부르는 쪽이 나른다(`fefo_allocation.decided_at` 과
        같은 규율).
    :param maintain_fn: 물류 경계. 기본값이 실제 함수 자체다 — `None` 을 받지 않는다
        (`core/clock.py` · `verifier.py` 와 같은 규율).
    """
    open_connection = core_db.connection if borrow is None else borrow
    with ExitStack() as stack:
        try:
            conn = stack.enter_context(open_connection())
        except Exception as exc:  # noqa: BLE001 - 연결 실패가 그날을 통째로 세우면 안 된다.
            return MaintenanceOut(as_of=as_of, status="FAILED", reason=f"연결 실패: {exc}")

        try:
            result = maintain_fn(
                conn,
                sim_run_id=sim_run_id,
                as_of=as_of,
                # 셋 다 마스터가 정해 넘긴다. 물류에는 기본값이 아예 없다
                # (빠지면 `InvalidAutoMaintenanceRequest`).
                reason_code=FRESHNESS_EXPIRED,
                recorded_by=AUTO_MAINTENANCE,
                occurred_at=occurred_at,
            )
        except Exception as exc:  # noqa: BLE001 - 유지보수가 터져도 하루는 계속 간다.
            conn.rollback()
            return MaintenanceOut(
                as_of=as_of,
                status="FAILED",
                reason=f"유지보수가 터졌다: {type(exc).__name__}: {exc}",
            )

        # 한 사이클이 한 커밋이다. Lot 마다 나눠 적지 않는다 — 물류가 배치
        # 시작에서 잡아 둔 잠금 순서가 중간 커밋에 풀리면 다른 실행과 역전된다.
        conn.commit()

        if not result.lots:
            # 정상이다. 폐기대기도 없고 자리만 남은 Lot 도 없었다.
            return MaintenanceOut(
                as_of=as_of,
                status="NOTHING_DUE",
                reason=f"손댈 Lot 이 없었다 (본 Lot {result.examined_lots})",
                result=result,
            )
        return MaintenanceOut(
            as_of=as_of,
            status="RAN",
            reason=(
                f"{len(result.lots)}개 Lot 을 다뤘다 (본 Lot {result.examined_lots}"
                f" · 폐기 {result.disposed_qty_kg}kg · 자리 {len(result.emptied_pallet_ids)}장)"
            ),
            result=result,
        )
