"""마스터 실행 이력 조회 — 조회 연결을 빌려 `master_agent_runs` 를 읽는다.

공개 함수 하나가 조회 연결(autocommit) 하나를 빌려 `repository/runs.py` 에 넘긴다(함수마다
SELECT 하나). 없음을 `LookupError` 로 올리는 것과 행 → 값 모양 바꾸기는 여기서 한다.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from app.core import db as core_db
from app.core.settings import get_db_schema
from app.master.domain.runs import check_walk_scope
from app.master.repository.runs import (
    select_latest_run,
    select_run,
    select_run_counts_by_day,
    select_runs,
)
from app.master.schemas.runs import DayRunCount, MasterAgentRun


def get_run(run_id: UUID) -> MasterAgentRun:
    """UUID 로 실행 1건. 없으면 `LookupError`.

    없는 것을 빈 값으로 돌려주지 않는다. 화면이 "그런 실행이 없다" 와 "가져오지
    못했다" 를 구별할 수 있어야 한다.
    """
    schema = get_db_schema()
    with core_db.read_connection() as conn:
        row = select_run(conn, run_id, schema=schema)
    if row is None:
        raise LookupError(f"실행이 없다: {run_id}")
    return row  # type: ignore[return-value]


def get_run_by_request_id(request_id: str, *, cycle: str | None = None) -> MasterAgentRun:
    """업무 키로 가장 최근 실행 1건. 없으면 `LookupError`.

    같은 업무 키로 여러 번 돌면 행이 여럿이다 (append-only). "그 요청 어떻게 됐냐" 에는
    마지막 결과가 답이라 최신을 돌려준다. 전체가 필요하면 `list_runs(request_id=...)` 를
    쓴다.

    `cycle` 을 주는 쪽이 왜 중요한가.

      조회와 매입이 같은 업무 키를 쓴다. 둘 다 `make_request_id(as_of)` 로
      `REQ-20251231-0001` 을 만들고, 순번 관리는 호출자 몫이라 화면이 안 주면 같은
      값이 된다.

      조회도 이력에 적히므로 그 행이 최신이 되는 날이 생긴다. 그러면

      ```text
      결정 경로     승인할 실행을 찾다가 조회를 집는다 - 조회는 승인 대상이 아니다
      이력 화면     매입 실행을 보여줘야 할 자리에 조회가 뜬다
      ```

      기본값을 두지 않는다. 조용히 걸러 주면 새 호출자가 무엇을 보는지 모른 채 쓰게
      된다 - 부르는 쪽이 자기가 무엇을 찾는지 밝힌다.
    """
    schema = get_db_schema()
    with core_db.read_connection() as conn:
        row = select_latest_run(conn, request_id, cycle=cycle, schema=schema)
    if row is None:
        scope = "" if cycle is None else f" ({cycle})"
        raise LookupError(f"업무 키로 찾은 실행이 없다{scope}: {request_id}")
    return row  # type: ignore[return-value]


def list_runs(
    *,
    request_id: str | None = None,
    as_of: date | None = None,
    as_of_before: date | None = None,
    cycle: str | None = None,
    item: str | None = None,
    sim_run_id: str | None = None,
    limit: int = 50,
) -> list[MasterAgentRun]:
    """조건에 맞는 실행 목록. 최신부터.

    조건을 주지 않으면 전체에서 최신 `limit` 건이다. 필터는 전부 선택이고 주어진 것만
    AND 로 붙는다 - 없는 조건을 기본값으로 채우지 않는다.

    `as_of_before` 는 그 날 이전이다 (`<`). 오늘 실행이 어제까지 승인된 것을 물을 때
    쓴다 (#185) - 오늘 것을 같이 세면 자기 자신을 입력으로 먹는다. `as_of` 와 함께
    주면 둘 다 AND 로 걸린다.

    `sim_run_id` 도 기본값이 없다 (`Refs #150`). 안 주면 안 좁힌다 - 기존 호출을 안
    깨뜨리고, 무엇보다 "어느 실행인지 기록되지 않은" 1,206행을 조용히 감추지 않는다.

      주의: 이 인자로 검증 상태와 장기 상태가 갈리지는 않는다. 축이 붙은 행만 갈리고,
      축이 NULL 인 옛 행은 어느 값으로도 안 걸린다 - 그것이 사실이다.

    바로 아래 `count_runs_by_day` 는 태도가 반대다. 거기는 `sim_run_id` 가 필수이고
    없으면 거부한다. 물음이 다르기 때문이다.

       ```text
       list_runs           "무슨 행이 있나"     → 안 좁히는 것이 정직하다
       count_runs_by_day   "이 걷기가 어땠나"   → 어느 걷기인지 없으면 물음이 안 선다
       ```
    """
    schema = get_db_schema()
    with core_db.read_connection() as conn:
        rows = select_runs(
            conn,
            request_id=request_id,
            as_of=as_of,
            as_of_before=as_of_before,
            cycle=cycle,
            item=item,
            sim_run_id=sim_run_id,
            limit=limit,
            schema=schema,
        )
    return [row for row in rows]  # type: ignore[misc]


def count_runs_by_day(
    *,
    sim_run_id: str,
    start: date,
    end: date,
) -> list[DayRunCount]:
    """한 걷기의 날짜별 집계. 행이 있는 날만, 오래된 날부터.

    `sim_run_id` 가 필수다 — `list_runs` 와 반대다 (`Master 19.0` §3.3).

      성적표는 "이 걷기가 어땠나" 를 묻는다. 안 좁히면 사람이 손으로 부른 행과 옛
      실험이 같이 세어지고, 행 수가 걷기의 성적으로 읽힌다. 실측(2026-09-09)으로 이 표
      1,322행 중 걷기는 116행이고 나머지 1,206행은 축이 안 실린 행이다.

      빈 값을 조용히 전체로 바꾸지 않는다. `""` 도 `None` 도 거부한다 — 기본값을 주면
      새 호출자가 무엇을 세는지 모른 채 쓰게 된다.

      축이 NULL 인 행은 어느 걷기에도 안 걸린다. `sim_run_id = %s` 는 NULL 을 안
      집는다. 그것이 사실이고, 감추는 것이 아니라 못 답하는 것이다.

    `limit` 이 없다. `list_runs` 의 기본 50 으로는 200일 걷기를 못 읽는다. 여기는
    집계라 결과가 날 수만큼이고 행 수를 따라 늘지 않는다.

    파이썬에서 세지 않는다. 600행을 끌어와 세면 "몇 행을 읽었나" 와 "몇 행이 있나" 가
    갈릴 자리가 생기고, 걷기가 길어질수록 그 자리가 커진다.

    :raises ValueError: `sim_run_id` 가 비었거나 `end` 가 `start` 보다 앞일 때
        (`check_walk_scope`).
    """
    axis = check_walk_scope(sim_run_id=sim_run_id, start=start, end=end)

    schema = get_db_schema()
    with core_db.read_connection() as conn:
        rows = select_run_counts_by_day(
            conn, sim_run_id=axis, start=start, end=end, schema=schema
        )
    return [
        DayRunCount(
            as_of=row["as_of"],
            runs=row["runs"],
            end_codes=dict(row["end_codes"]),
            # 모양만 바꾼다 — 세는 것은 SQL 이 이미 다 했다.
            items=tuple(row["items"]),
            gate_blocked=bool(row["gate_blocked"]),
        )
        for row in rows
    ]
