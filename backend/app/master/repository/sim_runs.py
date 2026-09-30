"""
sim_run.py — **새 시뮬레이션 실행 한 행을 만든다.**

```text
build_sim_run_id(...)   이름을 짓는다          SIM-{RUN_TYPE}-{YYYYMM}[-{구분}]
create_sim_run(conn, ...)  sim_runs 에 한 행    부르는 쪽 커넥션으로 쓴다
```

🔴 **왜 이 파일이 필요한가.** `sim_runs` 에 INSERT 하는 코드가 저장소 전체에 **한
   곳도 없었다** (2026-09-10 실측 · 표에는 사람이 넣은 `SIM-BURNIN-202512` 한 행뿐).
   그래서 새 구간을 걸으려면 사람이 SQL 을 직접 쳐야 했고, **어떤 실행을 어떤 설정으로
   만들었는지가 저장소 밖에 남았다.**

---

★★ **이름을 파싱하지 않는다.** 종류를 알아야 하면 `run_type` 칸을 읽는다.

```text
❌ sim_run_id.split("-")[1] == "BURN_IN"     이름이 사실의 주인이 된다
🟢 row["run_type"] == "BURN_IN"              칸이 주인이다
```

  ⚠️ 이름은 **사람이 읽는 것**이다. 파싱하는 순간 `SIM-WALK-202601-재시도` 같은
    구분자가 붙은 이름 하나로 판정이 갈리고, 그때는 이름을 못 바꾼다.

---

🔴 **`company_persona_id` 를 지어내지 않는다.** `sim_runs.company_persona_id` 는
   NOT NULL 이고 `company_personas` 를 참조하는 FK 다 — 없는 키를 넣으면 FK 가 막고,
   있는 키를 아무거나 집으면 **남의 회사 설정 위에서 걸은 성적**이 남는다.
   인자로 받고, 없으면 터진다.

🔴 **`config_json` 을 해석하지 않는다.** 백필 규칙(`#518` · `#527`)이 그 칸에 들어가고
   그 규칙의 주인은 `backfill.py` 다. 이 파일은 **받은 것을 그대로 적기만** 한다 —
   여기서 키를 검사하거나 기본값을 채우면 규칙의 주인이 둘이 된다.

★ **시계를 안 읽는다.** `started_at` · `finished_at` 도 인자다. 여기서 `now()` 를
  읽으면 같은 실행을 두 번 만들 때 값이 갈리고, 그 사실이 어디에도 안 남는다
  (`backtest_runner` 가 `now` 를 인자로 받는 것과 같은 규율).

🔴 **commit 하지 않는다.** 커밋은 부르는 쪽이 한다 — `persist_purchases` 와 같다.
   여기서 커밋하면 실행 행만 먼저 확정되고, 뒤이어 초기 상태 적재가 터졌을 때
   **아무 장부도 없는 실행**이 남는다.

★ 2026-09-30 재구성 BL-018: `master/sim_run.py` 에서 옮겼다. 역할이 다른 부분은 갈랐다 —
  `domain/sim_run.py`. 무엇이 어디로 갔는지는 설계서 대응표 `master/` 절. 함께 모은 것:
  `master/sim_run_runner.py` 의 `assert_openable`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

from app.core.settings import get_db_schema
from app.master.repository.sim_run_open import RUN_TABLE


def create_sim_run(
    conn: Any,
    *,
    sim_run_id: str,
    company_persona_id: str,
    run_type: str,
    period_start: date,
    period_end: date,
    as_of: date,
    status: str,
    financing_mode: str,
    config_json: Mapping[str, Any],
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    note: str | None = None,
) -> str:
    """`sim_runs` 에 **한 행**을 만든다. 만든 실행의 이름을 돌려준다.

    ★ **`ON CONFLICT` 를 안 붙인다.** 같은 이름이 이미 있으면 터져야 한다 —
      조용히 넘기면 **다른 설정으로 만들려던 실행**이 옛 행 위에 앉고, 그 뒤의
      179일이 어느 설정으로 걸린 것인지 아무도 못 답한다.

    :param config_json: 실행 설정. 🔴 **그대로 적는다** — 여기서 키를 보지 않는다
        (모듈 docstring). 백필 규칙의 주인은 `backfill.py` 다.
    :raises ValueError: `sim_run_id` · `company_persona_id` · `run_type` ·
        `status` · `financing_mode` 중 빈 것이 있거나 기간이 거꾸로일 때.
        **빈 값을 메우지 않는다.**
    """
    for 이름, 값 in (
        ("sim_run_id", sim_run_id),
        # 🔴 **여기가 FK 다.** 기본값을 두면 *"안 준 실행"* 이 조용히 남의 회사
        #    설정 위에 앉고, FK 가 안 막으므로 아무 오류도 안 난다.
        ("company_persona_id", company_persona_id),
        ("run_type", run_type),
        ("status", status),
        ("financing_mode", financing_mode),
    ):
        if not 값 or not 값.strip():
            raise ValueError(f"{이름} 없이 실행을 만들 수 없다 — 없는 값을 지어내지 않는다")
    if period_start > period_end:
        raise ValueError(
            f"실행 기간이 거꾸로다: {period_start.isoformat()} ~ {period_end.isoformat()}"
            " — 어느 쪽이 시작인지를 여기서 정하지 않는다"
        )

    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                INSERT INTO {}.sim_runs (
                    sim_run_id, company_persona_id, run_type,
                    period_start, period_end, as_of,
                    status, financing_mode, config_json,
                    started_at, finished_at, note
                )
                VALUES (
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s
                )
                """
            ).format(sql.Identifier(get_db_schema())),
            [
                sim_run_id,
                company_persona_id,
                run_type,
                period_start,
                period_end,
                as_of,
                status,
                financing_mode,
                # 🔴 **받은 것을 그대로 싣는다.** `dict(...)` 로 한 겹 벗기지도
                #    기본값을 채우지도 않는다 — 규칙의 주인은 이 파일이 아니다.
                Jsonb(config_json),
                started_at,
                finished_at,
                note,
            ],
        )
    return sim_run_id


def assert_openable(conn: Any, *, sim_run_id: str, reset: bool) -> None:
    """이미 있는 실행 위에 **조용히 앉지 않는다.**

    ★ **번인 가드를 여기서 안 만든다.** `reset_sim_run_ledger` 가 이미 막는다 —
      두 곳에서 막으면 언젠가 한쪽만 고쳐진다.
    """
    if reset:
        return
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL("SELECT 1 AS present FROM {}.{} WHERE sim_run_id = %s").format(
                sql.Identifier(get_db_schema()),
                sql.Identifier(RUN_TABLE),
            ),
            [sim_run_id],
        )
        if cursor.fetchone() is not None:
            raise ValueError(
                f"실행 {sim_run_id!r} 이 이미 있다 — 조용히 덮지 않는다."
                " 다시 열 것이면 --reset 을 줘라 (🔴 그 실행의 장부를 지운다"
                " · 되돌릴 수 없다)"
            )
