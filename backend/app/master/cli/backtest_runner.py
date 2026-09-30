"""
backtest_runner.py — **범위를 하루씩 걸으며 하루 실행을 부른다.**

```text
walk(sim_run_id=..., start=..., end=..., now=...)   start..end 를 하루씩 걷는다
                                                    개장일마다 run_scheduled_day 를 부른다
```

🔴 **실행 축이 인자다. 기본값이 없다** (2026-09-10). 179일을 **어느 실행에 쌓는지**가
   곧 그 곡선의 정체다 — 기본값을 두면 말 안 하고 번인(`SIM-BURNIN-202512`)에 쌓을 수
   있고, 사람이 심어 둔 30일 위에 179일이 겹쳐 앉는다.

🔴 **여기에 판단이 없다. 개장도 입고도 채권도 수금도 출고도 여기서 다시 짜지 않는다.**

  그 순서는 `scheduler.run_scheduled_day` 가 이미 안다. 이 파일은 **날짜 축**만
  진다 — 어느 날을 걷고 어느 날을 건너뛰고 어디서 멈추는가.

★ **왜 저장소에 세우나.** 179 영업일 걷기를 임시 스크립트로 돌려 성적을 냈는데,
  그 스크립트가 저장소 밖이라 다른 사람이 같은 걸음을 못 걷는다. 성적만 남고
  **그 성적을 낸 걸음이 안 남는 것**이 문제다.

⚠️ **그 임시 스크립트는 `run_procurement` 을 직접 불렀다.** 그래서 개장 · 입고 ·
  채권 · 수금 · 장부 관문이 한 번도 안 돌았고, *"사고 0건"* 은 **그 다섯 단계를 안 탄
  채로** 나온 숫자였다. 이 파일이 고치는 것이 정확히 그것이다.

```text
❌ run_procurement(ProcurementRunRequest(as_of=d, ...))   개장·입고·채권·수금·장부게이트를 건너뛴다
🟢 run_scheduled_day(action, ...)                          오늘 선 순서 전부를 안다
```

---

🔴 **승인은 명시로만 켠다** (2026-09-11).

```text
--auto-approve 를 **안 주면**   승인 함수가 이름조차 안 불린다 · 한 건도 안 선다
--auto-approve 를 주면          각 판단 **바로 뒤**에 규칙대로 승인이 선다
```

★★ **설정에 규칙이 있다고 켜지지 않는다.** *"있으니까 한다"* 는 암묵 스위치이고,
  그러면 설정을 실험하려고 넣은 사람이 **승인까지 하게 된다.**

⚠️ **반대로, 켰는데 규칙이 없으면 걷기 전에 막는다.** 조용히 걸으면 179일 뒤에
  0건이 나오고 사람은 그것을 *"돌았는데 해당이 없었구나"* 로 읽는다.

---

🔴 **물류 유지보수도 명시로만 켠다** (2026-09-11).

```text
--auto-maintain 를 **안 주면**   유지보수 함수가 이름조차 안 불린다 · 한 Lot 도 안 없앤다
--auto-maintain 를 주면          **개장 바로 뒤**에 그날 자리를 비운다
```

★★ **승인보다 더 조심할 자리다.** 승인은 `master_decisions` 에 한 줄이 남는
  것이지만 폐기는 **물건이 없어진다** — 물류가 *"되돌릴 경로가 없다(`ADJUST_IN`
  없음 · 실사 제외)"* 고 못박았다.

⚠️ **켰는데 버릴 것이 없어도 막지 않는다.** `--auto-approve` 와 축이 다르다 —
  저쪽은 규칙 파일이 있어야 성립하고, 이쪽은 확인할 설정이 없다. 버릴 것이 없는
  날은 사고가 아니라 정상이고, 그 사실은 `NOTHING_DUE` 가 말한다.

---

🔴 **운영비 지급도 명시로만 켠다** (2026-09-17).

```text
--auto-settle-expenses 를 **안 주면**   지급 함수가 이름조차 안 불린다 · 현금이 안 움직인다
--auto-settle-expenses 를 주면          **마감 바로 앞**에 지급일이 된 비용을 지급한다
```

★★ **이 자리가 없어서 `operating_expense_cash_out_krw` 가 늘 0원이었다.** 발생도
  지급도 재무 원장에 이미 있었고, **부르는 자리 하나**가 없었다.

🔴 **지급이 터진 날은 그날 마감이 `BLOCKED` 다.** 유지보수·전이와 태도가 다르다 —
  저쪽은 터져도 하루가 계속 가지만, 이쪽은 현금이 움직인 뒤라 그냥 넘기면
  **«현금은 줄었는데 비용은 0원»** 인 기록이 손익 곡선의 확정값으로 앉는다.

⚠️ **켰는데 지급할 것이 없어도 막지 않는다.** `--auto-maintain` 과 같은 축이다 —
  그 날은 사고가 아니라 `NOTHING_DUE` 다.

---

🔴 **`now` 를 인자로 받는다. 시계를 안 읽는다.**

  `plan_next_action` 이 마감(10:30)과 비교하는 값이 `now` 다. 이 파일이 시계를
  읽으면 *"마감 뒤"* 상태를 검사가 만들 수 없고, 걷기 결과가 **돌린 시각에 따라
  갈린다.**

  ★ **받은 시각에서 시각만 떼어 그날에 붙인다.** 걷는 날마다 마감 비교가 그날의
    10:30 을 봐야 하기 때문이다. 받은 `now` 를 그대로 179일에 다 쓰면 첫날 말고는
    전부 *"마감이 한참 지난 미래"* 가 된다.

  ⚠️ 시간대는 받은 값의 것을 그대로 나른다 (`timetz()`). 여기서 `ZoneInfo` 를
    새로 만들지 않는다 — 시간대의 주인도 `clock.py` 하나다.

  🔴 **받은 `--now` 를 요약에 찍고 원장에 남긴다** (2026-09-13).

```text
요약    기준시각  <받은 문자열> · 날마다 HH:MM · 마감 10:30 뒤 / 🔴 전
원장    sim_runs.config_json.provenance.walked_now   ← 걷기 첫 날 전에 · 받은 문자열 그대로
```

  ★★ V8(16:00) 과 V9①(09:00) 이 같은 코드로 통째로 갈렸는데 그 값이 어디에도 안
    남아 있었다. **적는 것은 걷기다** — 진짜 값을 아는 것은 걷기뿐이다
    (`walk_provenance`). 이미 **다른** 값이 있으면 걷기 전에 막는다.

---

🔴 **달력을 못 읽으면 멈춘다. 건너뛰지 않는다.**

```text
is_market_open(day) == False   → 건너뛴다 (skipped_days 에 남는다)
CalendarNotCovered             → 🔴 멈추고 사유를 낸다
```

★ 그 예외는 *"장이 서는지 아닌지를 이 표로는 말할 수 없다"* 는 뜻이다. 건너뛰면
  **모르는 날을 안 서는 날로** 바꾸는 것이고, 그러면 성적표의 분모가 조용히 줄어든다.

---

🔴 **하루가 터져도 다음 날을 계속 걷는다. 다만 상한을 둔다.**

  ★ 하루가 터졌다고 걷기를 멈추면 179일 중 3일째에서 끝나고 나머지를 못 본다.

  ⚠️ **그런데 상한이 없으면 반대로 망가진다.** 장부가 깨진 채 179일을 걸으면 사고
    목록이 179줄이고 아무도 안 읽는다. 그리고 그 179줄은 **한 가지 사실**이다 —
    첫날 깨진 것이 안 고쳐졌다는 것. 연속 실패가 상한에 닿으면 멈추고 사유를 낸다.

---

🔴 **현금 축을 읽는다. 세지 않는다** (2026-09-12).

```text
현금        {매입유출: n · 물류유출: n · 인건이자: n · 수금: n · 순현금: n · 기말잔액: n}
현금항등식  Δ잔액 n · Σ순현금 n · 차이 n · 어긋난 날 n일 → 🟢 성립 / 🔴 깨짐
```

★★ **걷기가 현금 축을 아예 안 보고 있었다.** 칸은 `daily_closings` 에 처음부터
  있었고, V7 에서 매입 현금유출 27,122,228 원이 **흐름에는 잡히는데 잔액에서 안
  빠지는데도** 179일 동안 아무 줄도 그 사실을 말하지 않았다.

  ★ **고치는 것은 재무 몫이다** (recognition 과 settlement 사이 지급 전이). 이
    파일이 하는 것은 **걷기가 그 불일치를 스스로 말하게** 하는 것뿐이다.

🔴 **깨져도 `incidents` 에 안 넣는다.** 지급 전이가 서기 전에는 매일 깨지고, 사고로
  세면 `MAX_CONSECUTIVE_FAILURES` 에 걸려 **걷기가 못 끝난다** — 그러면 정본 판을
  못 돌린다. 🟢 센다 · 찍는다 · 판정을 낸다 / 🔴 멈추지 않는다 · `사고` 줄을 안
  건드린다.

⚠️ **어휘를 새로 만들지 않았다.** 세는 값은 전부 `scheduler` 가 낸 것 그대로다
  (`DayRunOutcome.action` · `ItemRunOutcome.end_code` · `procurement_status` 의
  `NOT_ATTEMPTED` · `sales_status` 의 세 값 · `outbound_status` 의 네 값 ·
  `RetryOut.outcomes` 의 네 값 · `MaintenanceOut.outcomes` 가 나르는
  `MaintenanceOutcome` 의 네 값 · `failed_items`). 그 네 값은 **접지
  않고 그대로 센다** — `NOTHING_DUE`(없다)와 `FAILED`(못 했다)와
  `NOT_ATTEMPTED`(안 했다)를 묶으면 손익 곡선이 왜 평평한지를 성적표가 못 답한다.
  이 파일이 새로 만든 말은 걷기 자체에 관한 것
  뿐이다 (`skipped_days` · `incidents` · `stopped_reason`) — 그것은 `scheduler` 가
  모르는 사실이다. 스케줄러는 하루만 알지 **범위를 모른다.**

★ 2026-09-30 재구성 BL-018: `master/backtest_runner.py` 에서 옮겼다. 역할이 다른 부분은 갈랐다 —
  `cli/console.py`; `report/walk_summary.py`. 무엇이 어디로 갔는지는 설계서 대응표 `master/` 절.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from app.core import db as core_db
from app.master.cli.console import use_utf8_output
from app.master.domain.backfill import BackfillRuleMissing, BackfillRules, SalesTermsRule
from app.master.domain.execution_day import CalendarNotCovered, MarketCalendar, MlBatchCalendar
from app.master.domain.forecast_gate import DayForecastReadiness
from app.master.domain.scheduler import DayRunOutcome, DayScope, plan_next_action
from app.master.readmodel.backfill_rules import read_run_rules
from app.master.readmodel.forecast_gate import day_forecast_readiness
from app.master.readmodel.ledger import read_walk_closings
from app.master.readmodel.market_calendar import get_market_calendar
from app.master.readmodel.ml_batch_calendar import get_ml_batch_calendar
from app.master.registry.bootstrap import wire_registries
from app.master.report.walk_summary import WalkIncident, WalkResult, format_summary, moment_on
from app.master.repository.walk_provenance import WalkedNowStamp
from app.master.service.sales_terms import read_run_sales_terms
from app.master.service.scheduler import DAILY_POLICY_VERSION, run_scheduled_day
from app.master.service.walk_provenance import record_walked_now

#: 연속 사고 상한. **닿으면 멈추고 사유를 낸다.**
#:
#: ★ **왜 5인가.** 영업일 닷새다. 한 주를 통째로 못 걸었으면 그 다음 날의 장부는
#:   이미 못 믿는다 — 안 받은 입고와 안 받은 수금이 닷새치 쌓인 위에서 판단이 돌고,
#:   거기서 나온 성적은 걷기의 성적이 아니라 **깨진 장부의 성적**이다.
#:
#: ⚠️ `calendar_walk.MAX_WALK_DAYS` 를 안 쓴다. 그 31 은 *"다음 실행일을 찾으러 앞으로
#:   걷는 상한"* 이고 축이 다르다 — 179일 걷기는 그 수를 당연히 넘긴다.
MAX_CONSECUTIVE_FAILURES = 5


def walk(
    *,
    sim_run_id: str,
    start: date,
    end: date,
    now: datetime,
    calendar: Callable[[], MarketCalendar] = get_market_calendar,
    ml_batch: Callable[[], MlBatchCalendar] = get_ml_batch_calendar,
    readiness: Callable[[date], DayForecastReadiness] = lambda as_of: day_forecast_readiness(
        as_of=as_of
    ),
    run_day_fn: Callable[..., DayRunOutcome] = run_scheduled_day,
    policy_version: str = DAILY_POLICY_VERSION,
    max_consecutive_failures: int = MAX_CONSECUTIVE_FAILURES,
    ticks: Callable[[], float] = time.monotonic,
    auto_approve: bool = False,
    rules_of: Callable[[str], BackfillRules] = read_run_rules,
    terms_of: Callable[[str], SalesTermsRule | None] = read_run_sales_terms,
    auto_maintain: bool = False,
    auto_settle_expenses: bool = False,
    closings_of: Callable[..., Sequence[Mapping[str, Any]]] = read_walk_closings,
    walked_now: str | None = None,
    record_now: Callable[..., WalkedNowStamp] = record_walked_now,
) -> WalkResult:
    """`start` 부터 `end` 까지 하루씩 걷는다. **개장일마다 하루 실행을 부른다.**

    🔴 **`run_scheduled_day` 를 부른다.** `run_procurement` 을 직접 부르지 않는다 —
      그러면 개장 · 입고 · 채권 · 수금 · 장부 관문을 통째로 건너뛰고, 그 위에서 나온
      *"사고 0건"* 은 아무것도 증명하지 않는다.

    :param sim_run_id: 어느 실행에 이 걸음을 쌓는가. 🔴 **기본값이 없다 — 안 주면
        터진다.** 179일을 어느 실행에 쌓는지가 곧 그 곡선의 정체이고, 기본값을 두면
        **말 안 하고 번인에 쌓을 수 있다.** 그러면 사람이 심어 둔 30일 위에 179일이
        겹쳐 앉고, 어느 행이 번인이고 어느 행이 걷기인지 되가를 방법이 없다.

        ★ **여기서 실행을 만들지 않는다.** 행을 세우는 것은 `sim_run.create_sim_run`
          이고, 이 파일은 **받은 축을 나르기만** 한다 — 걷기가 실행을 만들면 같은
          범위를 두 번 걸을 때마다 실행이 하나씩 늘어난다.

    :param now: 걷는 동안 쓸 시각. 🔴 **인자다 — 이 파일은 시계를 안 읽는다.**
        날짜는 안 쓰고 **시각만** 떼어 걷는 날마다 붙인다 (모듈 docstring).
    :param calendar: 개장 축. `is_market_open` 하나만 부른다.
    :param ml_batch: 배치 축 (2026-09-13). `has_ml_batch` 하나만 부른다 — 하루마다
        `scheduler.plan_next_action` 에 넘긴다. 🔴 **여기서 판정하지 않는다.** 걷기가
        배치 없는 날을 제 손으로 건너뛰면 그 날 장부(입고 · 수금 · 출고 · 마감)가
        통째로 빠진다 — 무엇을 돌지는 하루 실행이 `scope` 로 안다.
    :param readiness: 그날 예측 게이트. `wake_up` 과 같은 모양으로 받는다.
    :param run_day_fn: 하루 실행. 🔴 **기본값이 `run_scheduled_day` 자체다** —
        `None` 을 안 받는다 (`clock.py` · `verifier.py` 와 같은 규율).
    :param ticks: 소요 시간을 재는 단조 시계. 🔴 **벽시계가 아니다** — 날짜도
        시간대도 안 만들고 *"얼마나 걸렸나"* 만 답한다. 검사가 고정값을 꽂는다.
    :param auto_approve: 🔴 **기본이 거짓이다. 안 주면 승인 함수가 이름조차 안
        불린다** (2026-09-11). 켜면 각 판단 **바로 뒤**에 승인이 선다.

        ★★ **설정에 규칙이 있다고 켜지지 않는다.** *"있으니까 한다"* 는 암묵
          스위치이고, 그러면 설정을 실험하려고 넣은 사람이 **승인까지 하게 된다.**
          켜는 것은 명시로만이고, 그 명시가 이 인자 하나다.
    :param rules_of: 그 실행이 정한 **승인** 규칙을 읽는 자리. 🔴 **`auto_approve` 가
        거짓이면 한 번도 안 불린다** — 안 켠 걷기가 승인 규칙을 물을 이유가 없다.
    :param terms_of: 그 실행이 정한 **판매 상업 조건**을 읽는 자리 (2026-09-11).

        🔴 **`rules_of` 와 한 인자로 묶지 않는다.** 축이 다르다 — 저쪽을 부르는
          것은 *"안 켜고 걸으면 막는다"* 는 관문이라 **안 켜면 안 부르는 것이
          잠겨 있다.** 이쪽은 관문이 아니라 요청에 실릴 값이고, 승인을 안 켜도
          조건은 실려야 한다. 하나로 묶으면 *"승인을 켜야 조건이 실린다"* 가 되고
          그 사실은 아무 데도 안 적혀 있다.

        ★ **실행당 한 번 부른다.** 규칙은 실행에 속하지 날에 속하지 않는다.
    :param auto_maintain: 🔴 **기본이 거짓이다. 안 주면 유지보수 함수가 이름조차
        안 불린다** (2026-09-11). 켜면 **개장 바로 뒤**에 그날 자리를 비운다.

        ★★ **승인보다 더 조심할 자리다.** 폐기는 되돌릴 경로가 없다 —
          `ADJUST_IN` 도 실사도 없다고 물류가 못박았다.

        ⚠️ **`auto_approve` 처럼 걷기 전에 막는 관문이 없다.** 확인할 규칙 파일이
          없기 때문이다 — 버릴 것이 없는 날은 사고가 아니라 `NOTHING_DUE` 다.
    :param auto_settle_expenses: 🔴 **기본이 거짓이다. 안 주면 지급 함수가 이름조차
        안 불린다** (2026-09-17). 켜면 **마감 바로 앞**에 지급일이 된 운영비를 지급한다.

        ★★ **폐기보다 더 조심할 자리다.** 폐기는 물건이 없어지고 지급은 **현금이
          줄어드는데**, 되돌리는 경로가 재무에 없다 (`PAID → CANCELLED` 는 없다).

        🔴 **지급이 터진 날은 그날 마감이 `BLOCKED` 다.** 하루가 계속 가지 않는
          유일한 칸이고, 그 이유는 `scheduler.run_scheduled_day` 가 적어 뒀다.

        ⚠️ **`auto_maintain` 과 같은 축이다** — 걷기 전에 막는 관문이 없다. 확인할
          설정 파일이 없고, 지급할 것이 없는 날은 사고가 아니라 `NOTHING_DUE` 다.
    :param closings_of: 그 구간의 마감행을 읽는 자리 (2026-09-12). 🔴 **걷기 한
        판에 한 번, 다 걷고 나서 부른다** — 날마다 읽으면 같은 표를 179번 다시
        읽고, 그 중 하루만 다른 답이 오는 날이 오면 왜인지를 못 읽는다.

        🔴 **여기서 숫자를 만들지 않는다.** 읽은 값을 `WalkResult` 가 그대로 든다.

        ⚠️ **못 읽어도 걷기를 안 터뜨린다.** 179일을 다 걷고 마지막 조회에서 죽으면
          **성적을 통째로 잃는다** (`use_utf8_output` 이 막은 그 모양). 못 읽은
          사실은 `closings_reason` 이 든다 — *"없다"* 로 접지 않는다.
    :param walked_now: 사람이 준 `--now` **문자열 그대로** (2026-09-13). 주면
        `record_now` 가 그 실행의 `config_json.provenance.walked_now` 에 남긴다.

        🔴 **`now` 와 같은 시각이어야 한다.** 적은 값과 실제로 건 값이 갈리면 그
          칸은 없는 것보다 나쁘다 — 갈리면 걷기 전에 막는다.

        ⚠️ **안 주면 기록을 안 부른다.** 적을 문자열이 없다. 그 사실은 요약이
          「안 받았다」로 말한다 — 진입점(`main`)은 늘 준다.
    :param record_now: 기준 시각을 **재고 · 없으면 적는** 자리. 🔴 **걷기 첫 날
        전에 한 번 부른다** — 다 걷고 나서 부르면 연속 사고 상한에 걸려 멈춘 판에
        무엇으로 걸었는지가 안 남는다. 이미 **다른** 값이 있으면 그 자리가
        `WalkedNowConflict` 로 막고, 걷기는 한 날도 안 걷는다.
    :raises ValueError: 범위가 거꾸로거나 `now` 에 시간대가 없거나 `sim_run_id` 가
        빈 문자열일 때. **막고 사유를 낸다** — 조용히 바로잡지 않는다.

        🔴 **`auto_approve` 인데 그 실행이 규칙을 안 들었을 때도 막는다.** 조용히
          걸으면 179일 뒤에 승인 0건이 나오고, 사람은 그것을 *"승인이 돌았는데
          해당이 없었구나"* 로 읽는다 — 그때는 하루도 되돌릴 수 없다.
    """
    if not sim_run_id.strip():
        # 🔴 **상수로 메우지 않는다.** 조용히 번인으로 떨어지면 재무 채무와 매입
        #    원장이 서로 다른 실행에 앉고, 그때는 아무 오류도 안 난다.
        raise ValueError(
            "sim_run_id 없이는 걸을 수 없다 — 어느 실행에 쌓는지가 곡선의 정체다."
            " 실행을 먼저 만들고(`sim_run.create_sim_run`) 그 이름을 넘겨라"
        )
    if start > end:
        raise ValueError(
            f"걷기 범위가 거꾸로다: {start.isoformat()} ~ {end.isoformat()}"
            " — 어느 쪽이 시작인지를 여기서 정하지 않는다"
        )
    if now.tzinfo is None:
        raise ValueError(
            "시간대 없는 시각으로는 마감을 못 잰다 — 어느 지역의 10:30 인지가 없다."
            " walk 는 시계를 안 읽으므로 부르는 쪽이 시간대를 붙여 줘야 한다"
        )
    if max_consecutive_failures < 1:
        raise ValueError(
            f"연속 사고 상한이 {max_consecutive_failures} 다 — 1 보다 작으면 한 날도 못 걷는다"
        )
    if walked_now is not None and not _same_moment(walked_now, now):
        # 🔴 **칸이 거짓말을 하게 두지 않는다.** 원장에 적는 문자열과 날마다 붙여
        #    쓰는 시각이 갈리면, 그 칸을 믿고 읽은 사람이 오늘 같은 판을 또 버린다.
        raise ValueError(
            f"기준 시각 문자열 {walked_now!r} 과 걷는 시각 {now.isoformat()} 이 다르다"
            " — 원장에 적는 값과 실제로 건 값이 갈리면 그 칸이 거짓말을 한다"
        )

    # ── 🔴 **켰으면 걷기 전에 규칙을 확인한다** (2026-09-11) ────────────
    #
    # ⚠️ **조용히 아무것도 안 하면 사람이 「승인이 돌았는데 0건이구나」 로 읽는다.**
    #    그래서 첫날을 걷기도 전에 막는다 — 179일을 다 걷고 나서 알면 늦다.
    #
    # 🔴 **여기서 규칙을 지어내지 않는다.** 기본 규칙은 곧 업무 규칙이고, 그러면
    #    아무도 안 정한 규칙으로 곡선이 선다 (`backfill.py` 의 그 규율 그대로).
    if auto_approve:
        try:
            rules_of(sim_run_id)
        except BackfillRuleMissing as exc:
            raise ValueError(
                f"--auto-approve 인데 실행 {sim_run_id!r} 이 백필 규칙을 안 들었다: {exc}"
                " — 규칙을 실은 실행을 먼저 열어라(`sim_run_runner --backfill-rules`)."
                " 조용히 0건으로 걷지 않는다"
            ) from exc

    # 🔴 **상업 조건은 걷기 전에 한 번 읽는다** (2026-09-11). 규칙은 실행에 속하지
    #    날에 속하지 않는다 — 날마다 읽으면 같은 설정을 179번 다시 읽고, 그러다
    #    하루만 다른 조건으로 도는 날이 오면 **왜 그런지를 설정만 보고는 못 읽는다.**
    #
    # ⚠️ **여기서 막지 않는다.** 조건이 없는 것은 사고가 아니라 종전 동작이다 —
    #    `auto_approve` 관문과 축이 다르다 (`terms_of` 설명).
    sales_terms = terms_of(sim_run_id)

    market = calendar()
    # ★ 개장 축과 같은 자리 · 같은 횟수로 한 번 만든다. 표를 읽는 것은 첫 물음 때다.
    batch = ml_batch()

    # ── 🔴 **기준 시각을 걷기 첫 날 전에 남긴다** (2026-09-13) ─────────────
    #
    # ★ 재는 것(이미 다른 값이면 막는다)과 쓰는 것을 **한 자리에서 같이** 한다.
    #   다 걷고 나서 쓰면 179일을 걷다 연속 사고 상한에 멈춘 판에 무엇으로 걸었는지가
    #   안 남는다 — 그리고 그 판이 **가장 먼저 그 질문을 받는 판**이다.
    #
    # 🔴 **막히면 여기서 나간다.** 한 실행을 두 기준 시각으로 이어 걸으면 절반은 마감
    #    전 · 절반은 마감 뒤인 판이 되고, 그 판은 아무것도 증명하지 않는다.
    if walked_now is not None:
        record_now(sim_run_id=sim_run_id, walked_now=walked_now)

    started_ticks = ticks()

    days: list[DayRunOutcome] = []
    skipped: list[date] = []
    incidents: list[WalkIncident] = []
    stopped_at: date | None = None
    stopped_reason: str | None = None
    in_a_row = 0

    day = start
    while day <= end:
        # ── ① 달력 ──────────────────────────────────────────────────
        try:
            is_open = market.is_market_open(day)
        except CalendarNotCovered as exc:
            # 🔴 **건너뛰지 않는다.** 못 읽은 날을 안 선 날로 바꾸면 분모가 줄어든다.
            stopped_at = day
            stopped_reason = f"개장 달력을 못 읽어서 멈춘다: {exc}"
            break
        if not is_open:
            skipped.append(day)
            day += timedelta(days=1)
            continue

        # ── ② 하루 ──────────────────────────────────────────────────
        action = plan_next_action(
            now=moment_on(day, now),
            as_of=day,
            calendar=market,
            ml_batch=batch,
            gate_result=readiness(day),
        )
        try:
            # 🔴 **받은 축을 그대로 넘긴다.** 여기서 상수를 다시 읽거나 이름을
            #    고쳐 짓지 않는다 — 그러면 걷기가 부른 하루와 걷기가 말한 실행이
            #    갈리고, 성적표가 자기가 무엇을 쟀는지 모르게 된다.
            outcome = run_day_fn(
                action,
                policy_version=policy_version,
                sim_run_id=sim_run_id,
                # 🔴 **받은 스위치를 그대로 넘긴다.** 여기서 규칙의 유무를 보고
                #    다시 정하지 않는다 — 그러면 스위치가 둘이 된다.
                auto_approve=auto_approve,
                # 🔴 **읽은 조건을 그대로 넘긴다.** 하루가 제 손으로 다시 읽지
                #    않는다 — 그러면 같은 설정의 주인이 둘이 되고, 하루를 부르는
                #    모든 검사가 조용히 실 DB 를 친다.
                sales_terms=sales_terms,
                # 🔴 **받은 스위치를 그대로 넘긴다.** 여기서 다시 정하지 않는다 —
                #    그러면 폐기를 켜고 끄는 자리가 둘이 된다.
                auto_maintain=auto_maintain,
                # 🔴 **받은 스위치를 그대로 넘긴다** (2026-09-17). 여기서 다시 정하지
                #    않는다 — 그러면 현금이 나가고 안 나가고를 정하는 자리가 둘이 된다.
                auto_settle_expenses=auto_settle_expenses,
            )
        except Exception as exc:  # noqa: BLE001 - 하루가 터져도 다음 날은 걷는다.
            # ★ **터진 날도 사고로 남고 걷기는 이어진다.** 여기서 raise 하면 나머지
            #   날을 통째로 못 본다.
            incidents.append(
                WalkIncident(as_of=day, reason=f"하루 실행이 터졌다: {type(exc).__name__}: {exc}")
            )
            in_a_row += 1
        else:
            days.append(outcome)
            reason = _incident_reason(outcome, scope=action.scope)
            if reason is None:
                in_a_row = 0
            else:
                incidents.append(WalkIncident(as_of=day, reason=reason))
                in_a_row += 1

        # ── ③ 연속 사고 상한 ────────────────────────────────────────
        if in_a_row >= max_consecutive_failures:
            stopped_at = day
            stopped_reason = (
                f"사고가 {in_a_row}일 연속이라 멈춘다 (상한 {max_consecutive_failures})"
                " — 이 뒤를 더 걸어도 같은 사실이 줄 수만 늘어난다"
            )
            break

        day += timedelta(days=1)

    # ── ④ 🔴 **현금 축을 읽는다. 여기서 세지 않는다** (2026-09-12) ─────────
    #
    # ★ 다 걷고 한 번 읽는다 — 걷는 동안 읽으면 그날 마감이 아직 안 선 날의 행을
    #   보게 되고, 그 빈 자리가 「마감이 안 돌았다」로 보인다.
    #
    # 🔴 **읽다 터져도 걷기를 안 터뜨린다.** 그리고 그 실패를 `incidents` 에도
    #    안 넣는다 — 사고 줄은 **걸음의 축**이고, 조회 실패는 걸음이 아니다.
    #    못 읽은 사실은 `closings_reason` 이 따로 든다.
    closings: tuple[Mapping[str, Any], ...] = ()
    closings_reason: str | None = None
    try:
        closings = tuple(closings_of(sim_run_id=sim_run_id, start=start, end=end))
    except Exception as exc:  # noqa: BLE001 - 성적표를 통째로 잃는 것이 더 나쁘다.
        closings_reason = f"{type(exc).__name__}: {exc}"

    return WalkResult(
        start=start,
        end=end,
        days=tuple(days),
        skipped_days=tuple(skipped),
        incidents=tuple(incidents),
        stopped_at=stopped_at,
        stopped_reason=stopped_reason,
        elapsed_seconds=ticks() - started_ticks,
        closings=closings,
        closings_reason=closings_reason,
        walked_now=walked_now,
    )


def _same_moment(walked_now: str, now: datetime) -> bool:
    """받은 문자열이 **걷는 시각과 같은 시각**을 말하는가.

    ★ **시각과 시간대 오프셋을 둘 다 본다.** 같은 순간이라도 오프셋이 다르면
      `timetz()` 가 다른 시각을 날마다 붙인다 — 그러면 걷기가 쓰는 시각이 갈린다.

    ⚠️ **읽지 못하는 문자열은 다르다고 본다.** 적을 값이 시각이 아니면 칸이 거짓말을 한다.
    """
    try:
        받은 = datetime.fromisoformat(walked_now)
    except ValueError:
        return False
    return (
        받은.tzinfo is not None
        and 받은.replace(tzinfo=None) == now.replace(tzinfo=None)
        and 받은.utcoffset() == now.utcoffset()
    )


def _incident_reason(outcome: DayRunOutcome, *, scope: DayScope) -> str | None:
    """이 날이 사고인가. 사고면 사유, 아니면 `None`.

    🔴 **`scheduler` 가 낸 값만 읽는다.** 여기서 상태 목록을 다시 세지 않는다 —
      세면 `_LEDGER_GAP_STATUSES` 가 두 곳에 생기고, 한쪽만 바뀌는 날이 온다.

    ```text
    action == BLOCKED                       달력이든 배치 축이든 게이트든 못 읽었다
    돌기로 했는데 procurement_status 가
      NOT_ATTEMPTED                         개장이 막혔거나 장부 관문이 돌아섰다
                                            🔴 scope 가 FULL 이든 LEDGER_ONLY 든 같다
    failed_items 가 비지 않았다              품목이 터졌다 (나머지는 돌았다)
    outbound_status == FAILED               나가려다 못 나갔다
    expense_settlement_status == FAILED     운영비를 못 지급했다 (2026-09-17)
    closing_status == FAILED                마감이 닫아 보다 터졌다 (2026-09-16)
    ```

    🔴 **마감 `FAILED` 는 사고다** (2026-09-16). `SIM-CHAIN-CHECK-0916` 에서 03-09 부터
      마감이 매일 `FAILED` 였는데 요약은 「사고 0건」 이었다. 그날 장부가 안 닫혔으면
      다음 날 판단은 안 닫힌 장부 위에서 돈다 — 조용히 계속 가는 것보다 연속 사고
      상한에 걸려 멈추는 쪽이 낫다.

    🔴 **운영비 지급 `FAILED` 도 사고다** (2026-09-17). 마감 `FAILED` 와 **같은 이유**다 —
      지급이 터진 날은 그날 마감이 `BLOCKED` 로 닫히고(`scheduler`), 다음 날 판단은
      **안 닫힌 장부 위에서** 돈다. 조용히 계속 가면 첫날 지급이 터진 판이 179일을
      `BLOCKED` 로 걷고 **끝에서야** 그 사실이 보인다.

      ★ **지급 단계의 어휘 중 `FAILED` 만 본다.** `NOT_ATTEMPTED` 는 안 켠 날이고
        `NOTHING_DUE` 는 지급일이 된 것이 없던 날이라 둘 다 정상이다 — 그 분포는
        `expense_settlement_statuses` 가 요약에 따로 찍는다.

    ⚠️ **마감 `BLOCKED` · `NOT_OPENED` 는 여기서 안 센다.**

      ```text
      개장이 막혀서 BLOCKED         앞 줄이 procurement_status 로 이미 잡는다
      장부 관문이 막아서 BLOCKED    앞 줄이 procurement_status 로 이미 잡는다
      지급이 터져서 BLOCKED         🔴 **위 줄이 지급 상태로 직접 잡는다** (2026-09-17)
      ```

      🔴 **세 번째 줄이 종전 전제를 깬다.** 마감 `BLOCKED` 를 안 세는 근거는 *"앞
        단계가 이미 막힌 날이라 그 사실을 앞 줄이 잡았다"* 였는데, 지급이 터진 날은
        **판단이 이미 `RAN`** 이라 앞 줄에 안 걸린다. 그래서 마감 어휘가 아니라
        **지급 어휘로** 잡는다 — 마감 상태를 여기서 다시 읽으면 앞의 두 줄이 사고
        둘로 세진다.

    ⚠️ **`WAIT` 은 사고가 아니다.** *"아직"* 이지 *"못"* 이 아니다. 그 구분이
      `scheduler` 가 다섯 어휘를 가른 이유이고, 여기서 접으면 그게 무의미해진다.

    ⚠️ **`NO_ML_BATCH` 로 판단을 안 돈 것은 사고가 아니다** (2026-09-13). 그 날
      `procurement_status` 는 `NOT_ATTEMPTED` 가 아니라 `NO_ML_BATCH` 이고, 그래서
      아래 줄에 안 걸린다.

      🔴 **그렇다고 `LEDGER_ONLY` 를 판정에서 통째로 빼지 않는다.** 그 날도 개장이
        막히거나 장부 관문이 돌아서면 `NOT_ATTEMPTED` 로 남고, **그것은 사고다** —
        빼면 배치 없는 토요일에 난 장부 사고가 안 세진다.

    ⚠️ **`NOT_A_MARKET_DAY` 도 사고가 아니다.** 달력 검사가 먼저 걸러서 여기까지
      오지도 않지만, 온다 해도 *"안 서는 날"* 은 정상이다.

    ⚠️ **출고 `NOTHING_DUE` 도 같은 결로 사고가 아니다.** *"나갈 것이 없다"* 는
      *"못 나갔다"* 가 아니다 — 예약이 아직 0행인 지금 그것을 사고로 세면 **매일이
      사고**가 되고, 사고 목록이 아무것도 안 가리킨다.

    🔴 **출고 `NOT_ATTEMPTED` 를 여기서 다시 세지 않는다.** 거기까지 못 간 날은
      판단 단계를 안 탄 날이고, 그것은 **위 줄이 이미 사고로 잡았다** — 겹쳐 적으면
      한 사실이 사고 둘로 세진다.
    """
    if outcome.action == "BLOCKED":
        return f"BLOCKED — {outcome.reason}"
    if scope != "NONE" and outcome.procurement_status == "NOT_ATTEMPTED":
        # ★ 개장 실패와 장부 관문을 한 값이 이미 가른다 — 둘 다 판단 단계를 안 탄다.
        return (
            f"판단 단계를 안 탔다 (개장: {outcome.day_open_status} ·"
            f" 입고: {outcome.inbound_status} · 채권: {outcome.receivable_status}"
            f" · 수금: {outcome.collection_status})"
            + (f" — {'; '.join(outcome.notes)}" if outcome.notes else "")
        )
    if outcome.failed_items:
        return f"품목이 터졌다: {', '.join(outcome.failed_items)}"
    if outcome.outbound_status == "FAILED":
        # ★ **사유를 여기서 짓지 않는다.** 무엇이 못 나갔는지는 `OutboundOut.reason`
        #   이 알고, `_stage` 가 그것을 note 로 실어 보냈다 — 그 값을 그대로 나른다.
        return "출고가 못 나갔다" + (f" — {'; '.join(outcome.notes)}" if outcome.notes else "")
    if outcome.expense_settlement_status == "FAILED":
        # ★ **사유를 여기서 짓지 않는다.** 무엇이 터졌는지는 `_settle_expenses` 가 만든
        #   그 한 줄이 알고, 같은 문장이 마감의 `ledger_gap` 으로도 갔다 — note 를 그대로 나른다.
        #
        # 🔴 **마감 줄보다 앞이다.** 지급이 터진 날은 마감이 `BLOCKED` 라 아래 줄에 안
        #    걸린다. 여기서 잡지 않으면 그 날은 **어느 줄에도 안 걸리고** 사고 0건이 된다.
        return "운영비를 못 지급했다" + (
            f" — {'; '.join(outcome.notes)}" if outcome.notes else ""
        )
    if outcome.closing_status == "FAILED":
        # ★ **사유를 여기서 짓지 않는다.** `_stage` 가 `ClosingOut.reason` 을 note 로 실었다.
        return "마감이 못 섰다" + (f" — {'; '.join(outcome.notes)}" if outcome.notes else "")
    return None


# ── 진입점 — 인자만 받는다 ─────────────────────────────────────────────
#
# 🔴 **로직이 여기 없다.** `walk()` 가 다 안다. 이 아래는 문자열을 날짜로 바꾸고
#    결과를 사람이 읽게 찍는 것뿐이다.


def _parser() -> argparse.ArgumentParser:
    """인자 정의. 🔴 **날짜에 기본값이 없다.**

    ⚠️ 기본 범위를 두면 **그 범위가 곧 업무 규칙이 된다** — 아무도 그것을 정한 적이
      없는데 성적표마다 그 범위가 찍히고, 나중에 *"왜 2월 7일부터인가"* 에 답할
      사람이 없다. 안 주면 막고 사유를 낸다.

    ⚠️ `--now` 도 기본값이 없다. 여기서 시계를 읽으면 **돌린 시각에 따라 성적이
      갈리고**, 그 사실이 성적표 어디에도 안 남는다.
    """
    parser = argparse.ArgumentParser(
        prog="python -m app.master.cli.backtest_runner",
        description="범위를 하루씩 걸으며 개장일마다 하루 실행(run_scheduled_day)을 부른다",
    )
    parser.add_argument(
        "--sim-run-id",
        required=True,
        help="어느 실행에 쌓는가 (예: SIM-WALK-202601) · 🔴 기본값 없음 — 안 주면 막는다",
    )
    parser.add_argument("--start", required=True, help="걷기 시작일 (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="걷기 종료일 (YYYY-MM-DD · 포함)")
    parser.add_argument(
        "--now",
        required=True,
        help="걷는 동안 쓸 시각 (ISO 8601 · 시간대 필수 · 예: 2026-09-07T10:35+09:00)",
    )
    parser.add_argument(
        "--max-consecutive-failures",
        type=int,
        default=MAX_CONSECUTIVE_FAILURES,
        help=f"연속 사고 상한 (기본 {MAX_CONSECUTIVE_FAILURES})",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        default=False,
        help=(
            "🔴 각 판단 바로 뒤에 규칙대로 승인한다 · master_decisions 에 행이 쓰인다"
            " · 안 주면 한 건도 승인하지 않는다"
        ),
    )
    parser.add_argument(
        "--auto-maintain",
        action="store_true",
        default=False,
        help=(
            "🔴 개장 바로 뒤에 그날 창고 자리를 비운다 · 폐기대기 Lot 이 없어진다"
            " (되돌릴 경로 없음) · 안 주면 한 Lot 도 건드리지 않는다"
        ),
    )
    parser.add_argument(
        "--auto-settle-expenses",
        action="store_true",
        default=False,
        help=(
            "🔴 마감 바로 앞에 지급일이 된 운영비를 지급한다 · 현금이 줄고 expenses 가"
            " PAID 로 바뀐다 (되돌릴 경로 없음) · 지급이 터진 날은 마감이 BLOCKED 다"
            " · 안 주면 한 건도 지급하지 않는다"
        ),
    )
    return parser


def main(argv: Sequence[str]) -> int:
    """진입점. **인자만 받아 `walk()` 에 넘긴다.**

    :returns: 끝까지 걸었고 사고가 없으면 0. 아니면 1 — **조용히 0 을 내지 않는다.**
    """
    args = _parser().parse_args(argv)
    use_utf8_output()
    # 🔴 **걷기 전에 등록소를 채운다.** 이 진입점은 `app/main.py` 를 안 거치므로,
    #    이 줄이 없으면 등록소가 전부 빈 채로 걷는다 — 걷는 날마다 *"하루 넘김
    #    미등록: finance, logistics"* 로 돌아서고 5일이 5일 다 사고였다 (2026-09-09).
    #
    # ★ **여기서 무엇을 등록할지 정하지 않는다.** 목록의 주인은 `bootstrap` 하나이고
    #   FastAPI 진입점도 같은 함수를 부른다. 두 진입점이 다른 세상을 보면 CLI 로 낸
    #   성적이 앱의 성적이 아니다.
    #
    # ⚠️ **`walk()` 안이 아니라 여기다.** `walk` 는 검사가 대역을 꽂아 부르는 함수이고,
    #   거기서 전역 등록소를 채우면 검사가 만든 세상을 조립 뿌리가 덮어쓴다.
    wire_registries()
    # ★ 풀은 이 걷기 동안만 쓴다 — 시작 때 열고, 정상 · 예외 어느 쪽으로 끝나도 닫는다.
    #   🔴 **하루를 한 트랜잭션으로 묶지 않는다.** 단계마다 제 연결을 빌리고 제 commit 을
    #      한다(설계서 §진입점 4). 풀은 연결을 다시 쓰게 할 뿐 경계를 바꾸지 않는다.
    with core_db.pool_lifespan():
        result = walk(
            sim_run_id=args.sim_run_id,
            start=date.fromisoformat(args.start),
            end=date.fromisoformat(args.end),
            now=datetime.fromisoformat(args.now),
            max_consecutive_failures=args.max_consecutive_failures,
            auto_approve=args.auto_approve,
            auto_maintain=args.auto_maintain,
            auto_settle_expenses=args.auto_settle_expenses,
            # 🔴 **받은 문자열 그대로 넘긴다** (2026-09-13). 위 `now` 는 파싱한 값이라
            #    `16:00` 이 `16:00:00` 이 되고, 사람이 준 것이 원장에서 사라진다.
            walked_now=args.now,
        )
    print(format_summary(result))
    return 0 if result.completed and not result.incidents else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
