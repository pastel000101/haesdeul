"""장부 조회 — 걷기 마감 행과 번인 실행을 읽는다.

SELECT 하나에 조회 연결 하나를 빌린다 — `get_burn_in` 은 실행 행과 마감 행을 따로 빌려
두 번 읽는다. SQL 은 `repository/ledger.py`.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.core import db as core_db
from app.core.settings import get_db_schema
from app.master.domain.sim_run import BURN_IN_SIM_RUN_ID
from app.master.repository.ledger import select_closings, select_sim_run, select_walk_closings


def read_walk_closings(*, sim_run_id: str, start: date, end: date) -> tuple[dict[str, Any], ...]:
    """그 실행의 `start..end` 마감행. 날짜순으로. 읽기만 한다.

    여기서 아무것도 안 센다. 합도 차이도 잔액도 만들지 않는다 — 금액의 주인은
    `daily_closings` 한 곳이고, 마스터는 그 값을 나르기만 한다
    (`master/schemas/closing.py` 의 `ClosingOut` 이 금액 칸을 하나도 안 든 것과 같은 규율).

    범위를 SQL 이 건다. 실행 하나에 번인 30일과 걷기 179일이 같이 앉을 수 있고, 앞
    구간의 행이 섞이면 기초잔액이 그 앞 어딘가의 값이 된다 — 그러면 Δ잔액이 걷기의
    것이 아니게 되고 항등식이 조용히 거짓말을 한다.

    정렬도 여기가 정한다. 부르는 쪽이 다시 정렬하면 순서의 주인이 둘이 된다.

    :returns: 마감행. 한 행도 없으면 빈 튜플이고 그것이 답이다 — 0 으로 메운 행을
        지어내지 않는다. "마감이 안 돌았다" 와 "돌았는데 0 이다" 는 다른 사실이고,
        지어내는 순간 그 둘이 화면에서 같아진다.
    """
    schema = get_db_schema()
    with core_db.read_connection() as conn:
        rows = select_walk_closings(
            conn, sim_run_id=sim_run_id, start=start, end=end, schema=schema
        )
    return tuple(dict(row) for row in rows)


def get_burn_in(sim_run_id: str = BURN_IN_SIM_RUN_ID) -> dict[str, Any]:
    """번인 한 건과 그 일별 마감 전부.

    `closed` 를 지우지 않는다. 마감되지 않은 날이 섞여 있으면 그 사실이 답의
    일부다 — 화면이 "아직 안 닫힌 날" 을 그대로 적을 수 있어야 한다.
    """
    schema = get_db_schema()
    with core_db.read_connection() as conn:
        run = select_sim_run(conn, sim_run_id, schema=schema)
    if run is None:
        raise LookupError(f"시뮬레이션을 찾을 수 없습니다: {sim_run_id}")

    schema = get_db_schema()
    with core_db.read_connection() as conn:
        closings = select_closings(conn, sim_run_id, schema=schema)
    return {"run": dict(run), "closings": [dict(row) for row in closings]}
