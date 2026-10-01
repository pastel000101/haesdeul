"""day_open.py — 하루가 넘어갈 때 **물류 runtime fixture 의 그날 행을 세운다.**

마스터가 `app/master/service/day_open.py` 에서 달력과 트랜잭션 경계를 쥐고, **무엇을
물려받고 무엇을 새로 둘지는 물류가 소유한다.** 이 파일이 그 물류 몫이다.

```text
마스터  언제 · 어느 날까지 · 한 커넥션 · 한 커밋      달력과 경계는 마스터 것이다
물류    logistics_runtime_fixture 의 그날 행 하나     무엇을 물려받을지는 물류가 안다
```

🟢 **새로 짠 SQL 이 아니다.**
   `database/seed/logistics/logistics_runtime_fixture_20260105_20260106.sql`
   이 이미 정확히 이 carry-forward INSERT 이고, 여기서는 그 모양과 그 값을 그대로
   옮겼다. 어느 칸을 물려받고 어느 칸을 새로 두는지는 **그 파일이 정한 그대로다.**

🔴 **입고 예정 두 칸을 이제 물려받지 않는다 (W3-3 · 2026-09-09).**

   ```text
   ~W3-2   물려받는다              in_transit 이 여러 날에 걸쳐 유지되는 상태였다
   W3-3~   CONFIRMED_ZERO · []     그 상태의 정본이 inbound_schedules 로 옮겨 갔다
   ```

   종전에는 물려받아야 했다 — 안 그러면 *"어제 승인된 입고 예정이 다음 날 조용히
   없어졌다"*. 그런데 **그 복제가 정확히 FIRSTINB 사고의 원인**이었다: 미래 날짜 행이
   먼저 열려 있으면 그 행은 나중에 난 승인을 모른 채 굳는다 (실측
   `INB-H1-REQ-FIRSTINB-20260113-1-1` — 01-15 행이 먼저 서서 01-14 승인을 못 받았다).

   지금은 일정 한 행이 날짜에 안 묶여 있고 Reader 가 날짜로 질의한다
   (`inbound_schedules.load_schedule_views`). 하루가 넘어가도 그 행은 그대로이므로
   **복제가 필요 없고, 복제하지 않으므로 사고도 재현되지 않는다.**

   ★ B-1(`tools.find_in_transit_schedule_gap`)도 여전히 통과한다 — 두 목록을 같은
     신규 표에서 만들고 `in_transit ⊆ confirmed_inbound` 라서다.

🔴 **`transition.py` 를 손대지 않았다.** `build_next_inventory` · `persist_inventory`
   는 승인이 부르는 경로이고 이 파일은 하루 넘김이 부르는 경로다. 두 경로가 같은 표의
   같은 행을 건드리지만 **쓰는 칸도 시점도 다르다** — 하루 넘김이 행을 세우고, 그날
   승인이 나면 `persist_inventory` 가 그 행의 **status 두 칸**만 세운다 (W3-3 부터
   업무 목록은 `inbound_schedules` 에만 적는다).

⚠️ **물류가 자기 판단으로 바꿀 수 있는 자리다.** 팀 리드 지시로 마스터 파트가 옮겨
   적었을 뿐, 어느 칸을 물려받을지는 물류 소유다. 바꿀 때 `in_transit` 과
   `confirmed_inbound` 를 함께 다루는 것만 지키면 된다 (위 B-1).

🔴 **실행 축을 여기서도 본다 (2026-09-06).** 종전에는 `is_open` 이 `as_of +
   usage_scope` 만 물었다. `uq_log_runtime_fixture` 가 `(sim_run_id, as_of,
   usage_scope)` 라 **다른 실행의 행이 같은 날에 공존할 수 있고**, 그러면 하루 넘김이
   *"남의 실행이 열려 있으니 내 실행도 열려 있다"* 로 답했다.

   ```text
   SIM-A / 2026-09-06 / AGENT_MVP_DEMO   있다
   SIM-B / 2026-09-06 / AGENT_MVP_DEMO   없다

   종전  SIM-B is_open → True     ← SIM-A 를 보고 답했다. 그리고 SIM-B 행은 영영 안 선다
   지금  SIM-B is_open → False    ← 자기 실행만 본다
   ```

   ★ **열렸는지 판정하는 눈과 실제 상태를 읽는 눈이 같아야 한다.** 그래서
     `readmodel/current.get_active_logistics_runtime_fixture` 도 같은 축을 받도록 함께 넓혔다
     — 한쪽만 넓히면 *"열렸다고 본 행"* 과 *"읽은 행"* 이 다시 갈린다.

★ 2026-09-30 재구성 BL-015: `logistics/day_open.py` 을 계층별로 나눴다. 이 파일에는 하루 넘김의
  **순서**가 남았다(`is_logistics_day_open` ·
  `open_logistics_day`). SQL 은 `repository/day_open.py`, 판정은 `domain/day_open.py`, 등록소 표면은
  `adapter.LogisticsDayOpening`.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.logistics.domain.day_open import day_open_from_runs
from app.logistics.repository.day_open import carry_forward_fixture, select_open_runs
from app.logistics.schemas.transition import LogisticsFixtureMissing
from app.logistics.schemas.vocabulary import USAGE_SCOPE


def is_logistics_day_open(conn: Any, *, as_of: date, sim_run_id: str | None) -> bool:
    """그날 **내 실행의** 물류 fixture 행이 이미 있는가.

    ```text
    persist_inventory   sim_run_id + as_of + usage_scope   마스터가 값을 준다
    repository 조회      sim_run_id + as_of + usage_scope   같은 축으로 넓혔다
    is_open             sim_run_id + as_of + usage_scope   ← 생성 인자로 받는다
    ```

    🔴 **`sim_run_id` 를 받았으면 조건에 넣는다.** Protocol 이 `as_of` 만 주므로
       종전에는 넣을 자리가 없었는데, 그 값은 호출마다 달라지는 값이 아니라 이
       배선이 어느 실행인지라 **생성 인자**가 맞는 자리다.

    ⚠️ **못 받았으면 실행 수를 세어 본다.** 하나면 종전과 같은 답을 내고, 둘 이상
       보이면 `LogisticsRunAmbiguous` 로 멈춘다 — 어느 실행의 *"열림"* 인지 모르는
       채로 True 를 내면 **다른 실행의 행 하나가 내 실행의 모든 날을 열린 것으로
       만든다** (그리고 내 행은 영영 안 선다: 마스터는 `is_open` 이 참인 날을
       anchor 로 잡고 그 뒤만 만든다).

    ★ **`SELECT DISTINCT sim_run_id … LIMIT 2` 다.** 세려는 것은 행 수가 아니라
      **실행 수**이고, 둘까지만 보면 가릴 수 있다.
    """
    실행들 = select_open_runs(conn, as_of=as_of, sim_run_id=sim_run_id)
    pinned = sim_run_id is not None

    return day_open_from_runs(실행들, pinned=pinned, as_of=as_of)


def open_logistics_day(
    conn: Any, *, as_of: date, carry_from: date, sim_run_id: str | None
) -> None:
    """`carry_from` 날 행을 물려받아 `as_of` 날 행을 만든다.

    🔴 **`carry_from` 행이 없으면 만들지 않고 예외를 던진다.** 물려받을 곳이 없는데
       행을 세우면 `evidence_grade` · `approved_by` · 세 status 를 기본값으로
       지어내게 되고, **지어낸 값이 그날의 사실로 남는다**
       (`LogisticsFixtureMissing` docstring 과 같은 이유다).

    ★ **INSERT 하나로 한다.** 값을 파이썬으로 읽어 와 다시 쓰면 그 사이에 값이
      모양을 바꾼다 (`jsonb` 왕복 · `Decimal` 왕복). `INSERT ... SELECT` 는 DB 안에서
      칸을 그대로 옮기므로 **물려받은 값이 어제 값과 같다는 것이 자명해진다.**

    ⚠️ **`ON CONFLICT DO NOTHING` 을 한 겹 더 둔다.** 마스터가 `is_open` 으로 이미
       거르지만, 두 번 열려도 두 번째가 아무 일도 안 하는 것이 여기서 보장된다.
       대상을 적지 않은 이유는 막을 것이 둘이라서다 — PK `fixture_id` 와 UNIQUE
       `uq_log_runtime_fixture (sim_run_id, as_of, usage_scope)`.

    🔴 **물려받을 행도 내 실행에서만 고른다.** `sim_run_id` 를 받았으면 가드
       (`is_open`)와 INSERT 의 `WHERE` 가 **둘 다** 그 실행으로 좁는다. 한쪽만
       좁히면 *"SIM-A 가 열려 있으니 통과, 그런데 만들 행은 SIM-B 것"* 처럼
       **본 행과 만든 행이 갈린다.**
    """
    # ★ 먼저 물려받을 행이 있는지 본다. `is_open` 과 같은 질문이라 같은 함수를
    #   쓴다 — 조건이 갈리면 "열렸다고 본 날" 과 "물려받을 수 있는 날" 이 달라진다.
    if not is_logistics_day_open(conn, as_of=carry_from, sim_run_id=sim_run_id):
        raise LogisticsFixtureMissing(
            # ★ 무엇이 없는지 보이게 적는다. `as_of` 만으로는 만들려던 날이 없다는
            #   것인지 물려받을 날이 없다는 것인지 가릴 수 없다.
            # ★ 실행도 적는다 — 다른 실행에는 그날 행이 있는데 내 실행에만 없는
            #   경우가 이제 정상적으로 존재하고, 그 둘을 메시지가 갈라야 한다.
            f"물려받을 물류 runtime fixture 행이 없다 (sim_run_id={sim_run_id},"
            f" carry_from={carry_from},"
            f" usage_scope={USAGE_SCOPE}). {as_of} 행을 만들지 않는다 —"
            " evidence_grade · approved_by · 나머지 status 는 물류 판단이다."
        )

    carry_forward_fixture(conn, as_of=as_of, carry_from=carry_from, sim_run_id=sim_run_id)
