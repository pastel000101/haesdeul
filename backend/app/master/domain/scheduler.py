"""하루 스케줄 판정 — 다음 행동 결정(plan_next_action), 범위 · 마감 시각, 하루 결과 모델.

★ 2026-09-30 재구성 BL-018: `master/scheduler.py` 에서 옮겼다 — `ExpenseSettlementStatus`,
  `EXPENSE_SETTLEMENT_STATUSES`, `_NO_ML_BATCH`, `_ML_BATCH_LATE`, `_ML_BATCH_MISMATCH`,
  `SchedulerAction`, `DayScope`, `_SCOPE_OF`, `scope_of`, `deadline_at`, `scheduled_items`,
  `ScheduledAction`, `plan_next_action`, `ItemRunOutcome`, `DayRunOutcome`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal, get_args

from app.contracts.commitment import ITEM_CODES
from app.core.clock import SEOUL
from app.finance.schemas.expenses import ExpenseSettlement
from app.master.domain.backfill import BackfillOut
from app.master.domain.execution_day import CalendarNotCovered, MarketCalendar, MlBatchCalendar
from app.master.domain.forecast_gate import DayForecastReadiness
from app.master.domain.schedule_times import SCHEDULE_DEADLINE, SCHEDULE_INTERVAL
from app.master.schemas.closing import ClosingOut
from app.master.schemas.inspection import InspectionOut
from app.master.schemas.maintenance import MaintenanceOut
from app.master.schemas.outbound_flow import OutboundOut
from app.master.schemas.pending_transition import RetryOut

#: 운영비 지급 한 칸의 결과 어휘 (2026-09-17). 🔴 **주인은 이 한 줄이다** —
#: `settle_expenses` 가 내는 값도 이것이고, 걷기 요약이 0건을 채울 때도 이것을 읽는다
#: (`inspection.INSPECTION_STATUSES` 와 같은 모양).
#:
#: ⚠️ **`NOT_ATTEMPTED` 가 여기 없다.** 그 말은 단계를 안 탄 날 `DayRunOutcome` 이 두는
#:   기본값이지 지급이 낸 값이 아니다 — 읽는 쪽이 기본값을 그대로 읽어 붙인다.
ExpenseSettlementStatus = Literal["RAN", "NOTHING_DUE", "FAILED"]
EXPENSE_SETTLEMENT_STATUSES: frozenset[str] = frozenset(get_args(ExpenseSettlementStatus))

#: `NO_ML_BATCH` 사유에 반드시 들어가는 문장.
#:
#: ★ 문자열을 상수로 둔 이유는 검사가 이 문장을 찾기 때문이다. 사유를 손으로 다시
#:   쓰면 철자가 갈리고, 그러면 그 날들을 나중에 못 센다.
#:
#: 🔴 **종전에는 `RUN_AND_RECORD` 사유에 실렸다** (2026-09-13 에 옮겼다). 두 축으로는
#:   그 날을 못 갈라서 *"마감까지 기다린 뒤 한 번 돌린 날"* 에 이 문장을 붙여 두었는데,
#:   배치 축(`ml_batch_calendar`)이 서면서 그 날은 `NO_ML_BATCH` 가 되었다.
#:   `RUN_AND_RECORD` 에 이 문장이 남으면 **배치가 도는 날인데 늦은 날**을 거꾸로 말한다.
_NO_ML_BATCH = "달력은 열렸는데 ML 배치가 없었다"

#: `RUN_AND_RECORD` 사유에 반드시 들어가는 문장 (2026-09-13).
#:
#: ★ 배치 축을 지난 날에만 이 어휘가 나오므로 **배치가 도는 날**이다 — 늦은 것이지
#:   없는 것이 아니다. 사람이 볼 곳은 ML 적재 쪽이다.
_ML_BATCH_LATE = "배치가 도는 날인데 마감까지 예측이 안 왔다"

#: `NO_ML_BATCH` 인데 예측이 와 있을 때 사유에 붙는 문장 (2026-09-13).
#:
#: 🔴 **조용히 넘기지 않는다.** 달력은 사람이 넣는 값이고, 어긋나면 그날 판단이 빠진
#:   채 정상처럼 보인다. 사람이 달력 쪽을 볼지 배치 쪽을 볼지 이 문장이 가른다.
_ML_BATCH_MISMATCH = "🔴 달력은 배치가 없는 날이라는데 예측이 와 있다 — 달력과 배치가 어긋났다"


#: 스케줄러가 답할 수 있는 **전부**. 일곱 번째를 만들지 않는다.
SchedulerAction = Literal[
    "RUN_NOW",
    "WAIT",
    "RUN_AND_RECORD",
    "NO_ML_BATCH",
    "NOT_A_MARKET_DAY",
    "BLOCKED",
]

#: 그 답이면 하루가 **어디까지** 도는가 (2026-09-13). 세 값뿐이다.
#:
#: ```text
#: FULL          개장부터 마감까지 전부 돈다
#: LEDGER_ONLY   장부만 돈다 — 매입 판단 · 매입 승인 · 판매 판단 · 판매 승인만 뺀다
#: NONE          서비스 함수를 하나도 안 부른다
#: ```
DayScope = Literal["FULL", "LEDGER_ONLY", "NONE"]

#: 🔴 **어휘와 범위를 잇는 유일한 자리다.** `run_scheduled_day` 도 걷기의 사고 판정도
#:   여기서 읽는다 — 두 곳이 각자 `action in (...)` 을 적으면 어휘가 느는 날 한쪽만
#:   옛 목록을 들고, 새 어휘가 **조용히** 한쪽에서 돌고 다른 쪽에서 안 돈다.
#:
#: 🔴 **`NO_ML_BATCH` 는 `FULL` 도 `NONE` 도 아니다.** `FULL` 이면 배치가 없는 날에
#:   판단이 돌아 `E4` 가 품목 수만큼 쌓이고, `NONE` 이면 장부까지 빠져 그날 출고 ·
#:   수금 · 마감이 사라진다 — 둘 다 에러가 안 난다.
_SCOPE_OF: dict[str, DayScope] = {
    "RUN_NOW": "FULL",
    "RUN_AND_RECORD": "FULL",
    "NO_ML_BATCH": "LEDGER_ONLY",
    "WAIT": "NONE",
    "NOT_A_MARKET_DAY": "NONE",
    "BLOCKED": "NONE",
}


def scope_of(action: SchedulerAction) -> DayScope:
    """그 답이면 하루가 어디까지 도는가. **모르는 어휘는 터진다.**

    ⚠️ **모르는 값을 `NONE` 으로 메우지 않는다.** 메우면 어휘를 하나 더 들인 날 그
      날이 조용히 안 돌고, 걷기는 그것을 *"안 도는 날"* 로 정상 처리한다.
    """
    return _SCOPE_OF[action]


def deadline_at(as_of: date) -> datetime:
    """그날의 마감 시각. **서울 시각으로 붙인다.**

    🔴 **시간대를 여기서 새로 만들지 않는다.** `clock.SEOUL` 을 가져다 쓴다 —
      `timezone(timedelta(hours=9))` 를 따로 들면 값이 같아서 아무도 못 보다가
      규칙이 바뀌는 날 조용히 갈린다 (`revalidation.py` 가 그랬다 · 2026-09-08).
    """
    return datetime.combine(as_of, SCHEDULE_DEADLINE, tzinfo=SEOUL)


def scheduled_items() -> tuple[str, ...]:
    """오늘 돌 품목. **`ITEM_CODES` 를 정렬해서 낸다. 목록을 다시 세지 않는다.**

    ★ `ITEM_CODES` 는 `frozenset` 이라 순서가 없다. 순서를 정해 두지 않으면 같은 날
      두 번 돌 때 품목 순서가 갈리고, 실패한 품목을 비교하기가 어려워진다.
    """
    return tuple(sorted(ITEM_CODES))


# ★ `ledger_gap_request_id` 는 여기서 안 짓는다 — **주인이 `run_repository` 다**
#   (2026-09-09 에 옮겼다). 그 키로 관문 행을 **되찾는** 쪽이 저장소이고, 저장소가
#   여기를 import 하면 `scheduler → persistence → run_repository` 와 고리가 된다.
#   이름은 그대로 살려 둔다 — 위 `__all__` 이 내보내고 검사가 이 이름으로 부른다.


# ── ① 결정 — 순수 함수 ─────────────────────────────────────────────────


@dataclass(frozen=True)
class ScheduledAction:
    """이번에 깨어나서 **무엇을 할지**. 부작용 없이 만들어진다.

    ★ **품목 내역을 같이 담는다.** `SOME_READY` 를 `RUN_NOW` 로 접어도 어느 품목이
      빠졌는지는 남아야 한다 — 빠진 품목은 그 자리에서 `MISSING → E4` 로 정직하게
      남고, 그 사실을 나중에 세려면 여기 있어야 한다.
    """

    as_of: date
    now: datetime
    action: SchedulerAction
    reason: str
    #: 마감 시각. **답과 함께 낸다** — 왜 `WAIT` 인지가 이 값과의 비교이기 때문이다.
    deadline: datetime
    #: 예측이 온 품목.
    ready_items: tuple[str, ...] = ()
    #: 확인했고 아직 안 온 품목. 🔴 `unreadable_items` 와 섞지 않는다.
    not_yet_items: tuple[str, ...] = ()
    #: 못 물어본 품목.
    unreadable_items: tuple[str, ...] = ()
    #: 다음 깨어남까지. **`WAIT` 일 때만 값이 있다.**
    retry_after: timedelta | None = None

    @property
    def scope(self) -> DayScope:
        """이번에 하루를 어디까지 돌리는가. **`scope_of` 를 그대로 읽는다.**

        🔴 **`WAIT` · `NOT_A_MARKET_DAY` · `BLOCKED` 는 안 돈다.** 특히 `WAIT` 은
          *"아직"* 이지 *"못"* 이 아니라, 여기서 돌면 `E4` 가 열두 건 쌓인다.

        🔴 **`NO_ML_BATCH` 는 장부만 돈다** (2026-09-13). 종전 `should_run` 이
          bool 이라 세 갈래를 못 담았고, 그래서 이름째 바꿨다 — 남겨 두면 누군가
          그것을 읽어 `NO_ML_BATCH` 를 둘 중 한쪽으로 접는다.
        """
        return scope_of(self.action)


def plan_next_action(
    *,
    now: datetime,
    as_of: date,
    calendar: MarketCalendar,
    ml_batch: MlBatchCalendar,
    gate_result: DayForecastReadiness,
) -> ScheduledAction:
    """이번에 깨어나서 무엇을 할지. **순수 함수다 — 아무것도 안 돌린다.**

    ★ **`now` 를 인자로 받는다.** 그래서 검사가 09:29 · 09:30 · 10:29 · 10:30 ·
      10:35 를 한 스위트 안에서 전부 지날 수 있다. 여기서 시계를 읽으면 검사는
      *"지금 몇 시인가"* 에 답이 끌려가고, CI 가 도는 시각마다 결과가 달라진다.

    ★ **세 축을 다 받는다.** 달력 둘은 물어봐야 알아서 객체로 받고
      (`CalendarNotCovered` 가 여기서 튄다), 게이트는 이미 답이 나와 있어 값으로 받는다.

    :param calendar: 개장 축. `is_market_open` 하나만 부른다.
    :param ml_batch: 배치 축 (2026-09-13). `has_ml_batch` 하나만 부른다.
        🔴 **기본값이 없다.** 이 함수는 순수 함수라 DB 를 타는 기본값을 둘 수 없고,
        빠뜨린 호출이 조용히 *"배치가 있다"* 로 떨어지면 토요일이 다시 `E4` 가 된다.
    :param gate_result: `day_forecast_readiness` 의 답. **미리 계산해서 넘긴다** —
        이 함수가 DB 를 타면 순수 함수가 아니게 되고, 검사가 대역을 끼울 자리가
        인자가 아니라 monkeypatch 가 된다.
    """
    if now.tzinfo is None:
        raise ValueError(
            "시간대 없는 시각으로는 마감을 못 잰다 — 어느 지역의 10:30 인지가 없다."
            " clock.seoul_now() 가 주는 값을 그대로 넘겨야 한다"
        )
    deadline = deadline_at(as_of)

    # ── ① 달력 ──────────────────────────────────────────────────────
    #
    # 🔴 **달력을 먼저 본다.** 휴장일이면 예측이 없는 것이 정상이고, 그 날 `NONE_READY`
    #    를 보고 기다리면 한 시간을 헛 깨어난 뒤 `E4` 까지 적는다.
    try:
        is_open = calendar.is_market_open(as_of)
    except CalendarNotCovered as exc:
        # 🔴 **fail-closed.** 달력을 못 읽은 것을 *"장이 선다"* 로도 *"안 선다"* 로도
        #    만들지 않는다. 재시도로 안 풀리므로 `WAIT` 이 아니다.
        return ScheduledAction(
            as_of=as_of,
            now=now,
            action="BLOCKED",
            reason=f"개장 달력을 못 읽었다: {exc}",
            deadline=deadline,
        )
    if not is_open:
        return ScheduledAction(
            as_of=as_of,
            now=now,
            action="NOT_A_MARKET_DAY",
            reason=f"{as_of.isoformat()} 은 장이 서지 않는다 — 기다릴 예측이 없다",
            deadline=deadline,
        )

    ready = gate_result.ready_items
    not_yet = gate_result.not_yet_items
    unreadable = gate_result.unreadable_items

    # ── 🟢 배치 — 🔴 **달력 뒤 · 게이트 앞** (2026-09-13) ────────────────
    #
    # ★★ **토요일이 `E4` 로 적히던 자리다.** 장은 서는데(`is_open=t`) 배치가 없는
    #   날(`is_survey=f`)을 게이트가 `NONE_READY` 로 보고, 스케줄러가 *"ML 이 늦는
    #   날"* 로 읽어 마감까지 기다렸다.
    #
    # 🔴 **게이트 앞이다.** 기다릴 예측이 없는 날은 게이트를 볼 이유가 없다 — 뒤에
    #    두면 마감 전 `--now` 로 건 걷기에서 그 날이 `WAIT` 이 되어 장부까지 빠진다.
    #
    # 🔴 **`is_survey` 를 읽는다. `is_open` 이 아니다.** 개장은 위에서 이미 봤고,
    #    같은 칸을 두 번 보면 이 분기는 한 번도 안 선다.
    try:
        has_batch = ml_batch.has_ml_batch(as_of)
    except CalendarNotCovered as exc:
        # 🔴 **fail-closed.** 못 읽은 것을 *"배치가 없다"* 로 만들면 DB 가 죽은 날 판단이
        #    통째로 빠지고, 그 사실이 `NO_ML_BATCH` 로 정상처럼 보인다.
        return ScheduledAction(
            as_of=as_of,
            now=now,
            action="BLOCKED",
            reason=f"배치 달력을 못 읽었다: {exc}",
            deadline=deadline,
            ready_items=ready,
            not_yet_items=not_yet,
            unreadable_items=unreadable,
        )
    if not has_batch:
        # ⚠️ **어긋남을 조용히 넘기지 않는다.** 달력은 배치가 없다는데 예측이 와 있으면
        #    사유에 적는다 — 그래도 `NO_ML_BATCH` 로 간다. 달력이 이 축의 주인이다.
        mismatch_note = f" · {_ML_BATCH_MISMATCH} (온 품목: {', '.join(ready)})" if ready else ""
        return ScheduledAction(
            as_of=as_of,
            now=now,
            action="NO_ML_BATCH",
            reason=(
                f"{_NO_ML_BATCH} — {as_of.isoformat()} 은 예측 배치가 원래 없는 날이라"
                f" 장부만 돌리고 판단은 안 돌린다{mismatch_note}"
            ),
            deadline=deadline,
            ready_items=ready,
            not_yet_items=not_yet,
            unreadable_items=unreadable,
        )

    # ── ② 게이트 ────────────────────────────────────────────────────
    if gate_result.readiness == "UNREADABLE":
        # 🔴 **`WAIT` 으로 접지 않는다.** DB 가 죽은 날 영원히 재시도하고 그 사실이
        #    아무 데도 안 남는 것이 정확히 이 한 줄에 달려 있다.
        return ScheduledAction(
            as_of=as_of,
            now=now,
            action="BLOCKED",
            reason=f"예측 게이트를 못 읽었다: {', '.join(unreadable)}",
            deadline=deadline,
            ready_items=ready,
            not_yet_items=not_yet,
            unreadable_items=unreadable,
        )

    if gate_result.readiness in ("ALL_READY", "SOME_READY"):
        # ★ **`SOME_READY` 도 돈다.** 빠진 품목은 `load_forecast` 가 `MISSING` 을 내고
        #   매입이 `RUNTIME_NOT_READY` → `E4` 로 정직하게 남긴다. 그 품목이 무엇인지는
        #   `not_yet_items` 가 나른다.
        missing_note = f" (빠진 품목: {', '.join(not_yet)})" if not_yet else ""
        return ScheduledAction(
            as_of=as_of,
            now=now,
            action="RUN_NOW",
            reason=f"예측이 왔다: {', '.join(ready)}{missing_note}",
            deadline=deadline,
            ready_items=ready,
            not_yet_items=not_yet,
            unreadable_items=unreadable,
        )

    # ── ③ NONE_READY — 마감 하나만 본다 ────────────────────────────
    #
    # ★ **상태를 안 본다.** 몇 번째 깨어남인지, 앞서 몇 번 기다렸는지를 안 세고
    #   `now` 와 마감만 비교한다. 그래서 프로세스가 죽었다 살아나도 답이 같다.
    if now >= deadline:
        return ScheduledAction(
            as_of=as_of,
            now=now,
            action="RUN_AND_RECORD",
            reason=(
                f"{_ML_BATCH_LATE} — 마감({deadline:%H:%M})이 지나"
                " 한 번 돌려 E4_NOT_STARTED 로 확정 기록한다"
            ),
            deadline=deadline,
            ready_items=ready,
            not_yet_items=not_yet,
            unreadable_items=unreadable,
        )
    return ScheduledAction(
        as_of=as_of,
        now=now,
        action="WAIT",
        reason=f"아직 예측이 안 왔다 — 마감({deadline:%H:%M}) 전이라 기다린다",
        deadline=deadline,
        ready_items=ready,
        not_yet_items=not_yet,
        unreadable_items=unreadable,
        retry_after=SCHEDULE_INTERVAL,
    )


# ── ② 실행 — 답을 따르기만 한다 ────────────────────────────────────────


@dataclass(frozen=True)
class ItemRunOutcome:
    """품목 하나의 실행 결과. **터진 것도 값으로 남는다.**"""

    item: str
    request_id: str
    #: `RAN` 은 판단이 끝까지 돌았다는 뜻이다. **좋은 답이었다는 뜻이 아니다** —
    #: `E4_NOT_STARTED` 도 `RAN` 이다. 못 돈 것은 `FAILED` 다.
    status: Literal["RAN", "FAILED"]
    #: 그 실행의 종료 코드. **못 돌았으면 `None`** — 코드가 없었다는 뜻이다.
    end_code: str | None = None
    reason: str = ""

    #: 그 실행이 부른 **부서별 호출마다 하나씩** — LLM 이 실제로 돌았나 (2026-09-12).
    #:
    #: 🔴 **값은 처음부터 `plan[].llm_status` 에 다 있었는데 여기서 끊겼다.**
    #:   `run_scheduled_day` 는 부서 응답을 손에 쥐고 `end_code` 만 떼어 갔고,
    #:   그래서 걷기 71영업일에 **재무 `FALLBACK` 918건 · `SUCCESS` 0건**이었는데
    #:   성적표에는 한 글자도 안 올라왔다 (`SIM-CHAIN-V6` 실측 2026-09-12).
    #:
    #: ★★ `plan.py:62` 가 이미 그 위험을 적어 뒀다 — *"Planner 가 죽어 규칙 경로로
    #:   떨어져도 **산출물은 멀쩡해 보인다**."* 멀쩡해 보이는 것을 멀쩡하지 않다고
    #:   말할 수 있는 유일한 값이 이것이고, 그 경고가 요약까지 안 올라와 있었다.
    #:
    #: ★ **이름의 주인은 `envelope.LLMStatus` 다.** 여기서 새 이름을 안 붙이고
    #:   부서가 낸 값을 그대로 나른다 (`end_code` 와 같은 규율).
    #:
    #: ⚠️ **못 돈 품목은 비었다.** 응답이 없으면 계획도 없다 — 그 자리는 `status`
    #:   가 이미 `FAILED` 라고 말한다.
    llm_statuses: tuple[str, ...] = ()

    #: 그 실행이 부른 **부서별 호출마다 하나씩** — 그 부서가 관측 기준시점을
    #: 실었나 (2026-09-12). 주인은 `AgentReply.observed_at` 이다.
    #:
    #: 🔴 **`llm_statuses` 와 모양은 같은데 `None` 을 안 버린다.** 저쪽은 빈
    #:   문자열을 걸러 내지만 이쪽의 `None` 은 **「안 쟀다」라는 값**이다 — 걸러
    #:   내면 걷기가 *"몇 건이 아직 안 쟀는지"* 를 영영 못 센다. 이 칸이 생긴
    #:   이유가 그 숫자다.
    #:
    #: ★ **`None` 은 「미래를 봤다」와 다른 사실이다.** 이 판은 세기만 한다 —
    #:   `observed_at > as_of` 를 막는 검사는 여기 없다. 아무도 안 채운 상태에서
    #:   걸면 전부 막힌다.
    #:
    #: ⚠️ **못 돈 품목은 비었다** — `llm_statuses` 와 같다. 계획이 없으면 셀
    #:   자리도 없고, 그것은 *"안 쟀다"* 가 아니라 **"셀 것이 없었다"** 다.
    observed_ats: tuple[date | None, ...] = ()


@dataclass(frozen=True)
class DayRunOutcome:
    """하루 실행 한 번의 결과. **어느 단계에서 무엇이 됐는지가 다 남는다.**

    ⚠️ 단계 상태의 `NOT_ATTEMPTED` 는 *"안 했다"* 다. `FAILED` 와 섞지 않는다 —
      개장이 막혀 안 한 것과 해 보고 터진 것은 다른 사실이다 (`DayOpenOut` 의
      `collection_seed_status` 와 같은 어휘).
    """

    as_of: date
    action: SchedulerAction
    reason: str
    day_open_status: str = "NOT_ATTEMPTED"
    #: 물류 유지보수 단계 (2026-09-11). 🔴 **개장 바로 뒤다 — 그날 자리를 비운다.**
    #:
    #: ★ **어휘를 새로 만들지 않았다.** `RAN` · `NOTHING_DUE` · `FAILED` 는
    #:   `MaintenanceOut.status` 그대로이고 (`closing_status` 가 `ClosingOut.status`
    #:   를 그대로 싣는 것과 같다), 단계를 안 탄 날은 이 클래스가 이미 쓰는
    #:   `NOT_ATTEMPTED` 다.
    #:
    #: ```text
    #: NOT_ATTEMPTED   안 켰다 — auto_maintain 이 거짓이었다 · 거기까지 못 갔다
    #: RAN             손댔거나 일부러 건너뛴 Lot 이 있었다 — 전부 성공이 아니다
    #: NOTHING_DUE     확인했고 할 것이 없었다 — 🟢 정상이다
    #: FAILED          하려다 터졌다 — 🔴 **그래도 하루는 계속 간다**
    #: ```
    #:
    #: 🔴 **`NOT_ATTEMPTED` 와 `NOTHING_DUE` 를 접지 않는다.** 앞은 *"안 켰다"* 이고
    #:   뒤는 *"켰는데 버릴 것이 없었다"* 다 — 폐기 0건의 이유가 그 둘로 갈린다.
    maintenance_status: str = "NOT_ATTEMPTED"
    #: 미적용 전이 재시도 단계 (2026-09-11). 🔴 **유지보수 뒤 · 입고 앞이다.**
    #:
    #: ★ **어휘를 새로 만들지 않았다.** `RAN` · `NOTHING_DUE` · `FAILED` 는
    #:   `RetryStatus` 그대로이고 (`closing_status` 가 `ClosingOut.status` 를 그대로
    #:   싣는 것과 같다), 단계를 안 탄 날은 이 클래스가 이미 쓰는 `NOT_ATTEMPTED` 다.
    #:
    #: ```text
    #: NOT_ATTEMPTED   거기까지 못 갔다 — WAIT · 휴장 · 개장 실패
    #: RAN             미적용을 찾아 다시 세웠다 — 전부 닿았다는 뜻이 아니다
    #: NOTHING_DUE     확인했고 미적용이 없었다 — 🟢 정상이다
    #: FAILED          찾다가 터졌다 — 🔴 **그래도 하루는 계속 간다**
    #: ```
    pending_transition_status: str = "NOT_ATTEMPTED"
    inbound_status: str = "NOT_ATTEMPTED"
    #: 물류 점검 #1 — 입고 직후 (2026-09-12 · #628). 🔴 **그날 점유가 뛴 자리다.**
    #:
    #: ★ **어휘를 새로 만들지 않았다.** `RAN` · `NOTHING_DUE` · `FAILED` 는
    #:   `InspectionOut.status` 그대로이고, 칸을 안 탄 날은 `NOT_ATTEMPTED` 다.
    #:
    #: ```text
    #: NOT_ATTEMPTED   거기까지 못 갔다 — WAIT · 휴장 · 개장 실패
    #: RAN             문제를 열었거나 갱신했거나 닫았다
    #: NOTHING_DUE     확인했고 손댈 것이 없었다 — 🟢 정상이다
    #: FAILED          보려다 터졌다 — 🔴 **그래도 하루는 계속 간다**
    #: ```
    #:
    #: 🔴 **스위치가 없다.** `auto_maintain` 과 다르다 — 이 칸은 **상태를 안 바꾸고**
    #:   표 하나에 행만 남기므로(폐기도 이동도 없다) 걷기 재현성을 안 해친다.
    #:   끄고 켜는 값을 두면 *"어제는 문제였는데 오늘은 아니다"* 가 설정으로 갈린다.
    inspection_inbound_status: str = "NOT_ATTEMPTED"
    #: 물류 점검 #2 — 출고 직후. 🔴 **닫히는 자리는 여기 하나다** (재탐지 → RESOLVED).
    inspection_outbound_status: str = "NOT_ATTEMPTED"
    #: 채권 발행 단계. 🔴 **수금보다 앞이다** — 채권이 서야 수금할 것이 있다.
    #:
    #: ★ **어휘를 새로 만들지 않았다.** `ReceivableOut.status` 의 다섯 값을 그대로
    #:   싣고, 단계를 안 탄 날은 이 클래스가 이미 쓰는 `NOT_ATTEMPTED` 다.
    receivable_status: str = "NOT_ATTEMPTED"
    collection_status: str = "NOT_ATTEMPTED"
    #: 판단 단계를 **탔는가**. 🔴 좋은 답이 나왔다는 뜻이 아니다 — 품목별 결과는
    #: `items` 가 나른다 (`ItemRunOutcome.status` 와 같은 어휘를 쓴다).
    #:
    #: ★ **어휘를 새로 만들지 않았다.** `NOT_ATTEMPTED` 는 이 클래스가 이미 세 단계에
    #:   쓰는 말이고 `RAN` 은 `ItemRunOutcome` 이 이미 쓰는 말이다. 단계를 안 탄
    #:   사실을 `items == ()` 으로만 두면 *"품목 목록이 비었다"* 와 구별이 안 된다.
    #:
    #: 🔴 **`NO_ML_BATCH` 가 네 번째 값이다** (2026-09-13). *"배치가 원래 없는 날이라
    #:   안 돌렸다"* — 값은 그날의 판단 어휘(`action`)를 그대로 싣는다. 새 문자열을
    #:   따로 적지 않는다.
    #:
    #:   ★★ **`NOT_ATTEMPTED` 로 두지 않는다.** 걷기가 *"돌기로 했는데 `NOT_ATTEMPTED`
    #:     → 사고"* 로 세므로 배치 없는 날 14일이 전부 사고가 된다. 그렇다고 그 날을
    #:     사고 판정에서 통째로 빼면 **개장이 막힌 진짜 사고**가 그 날엔 안 세진다 —
    #:     그래서 개장 실패 · 장부 관문은 그 날에도 `NOT_ATTEMPTED` 로 남는다.
    #:
    #: ⚠️ `sales_status` · `procurement_approval_status` · `sales_approval_status` 도
    #:   같은 날 같은 값을 든다. 네 칸이 한 사실을 말하는 이유는 요약이 네 줄에서 각자
    #:   세기 때문이다 — 한 줄에서만 사라지면 그 줄의 14일이 어디 갔는지 아무도 모른다.
    procurement_status: str = "NOT_ATTEMPTED"
    #: 판매 판단 단계를 **탔는가** (2026-09-10). 🔴 **매입 뒤 · 출고 앞이다.**
    #:
    #: ★ **어휘를 새로 만들지 않았다.** 셋 다 이 클래스가 이미 쓰는 말이다.
    #:
    #: ```text
    #: NOT_ATTEMPTED   안 했다 — 게이트가 WAIT 였다 · 관문이 막았다 · 품목이 없었다
    #: RAN             돌았다 — 좋은 답이었다는 뜻이 아니다
    #: FAILED          해 보고 터졌다 — 돈 품목이 **하나도** 없다
    #: NO_ML_BATCH     배치가 원래 없는 날이라 안 돌렸다 (2026-09-13 · `procurement_status` 참고)
    #: ```
    #:
    #: 🔴 **`FAILED` 는 「전부 터졌다」다.** 한 품목이 터진 날은 `RAN` 이고, 터진
    #:   품목은 `sales_items` 에 `FAILED` 로 남는다 — 매입 루프와 같은 규율이다.
    sales_status: str = "NOT_ATTEMPTED"
    #: 출고 단계를 **탔는가**. 🔴 `procurement_status` 와 **같은 모양·같은 어휘**다
    #: (`RAN` · `NOTHING_DUE` · `FAILED` · `NOT_ATTEMPTED`).
    #:
    #: ★ **어휘를 새로 만들지 않았다.** `NOTHING_DUE` 는 입고·수금이 이미 쓰는 말이고
    #:   나머지 셋은 이 클래스가 이미 쓴다. 판매 품목별 결과는 `OutboundOut.items` 가
    #:   나르고, 여기 다시 담지 않는다 — 같은 사실의 주인은 하나다.
    outbound_status: str = "NOT_ATTEMPTED"
    #: 운영비 지급 단계 (2026-09-17). 🔴 **마감 바로 앞이다 — 출고·점검 #2 뒤다.**
    #:
    #: ★★ **여기가 없어서 `operating_expense_cash_out_krw` 가 늘 0원이었다.**
    #:   `create_expense()` 도 `settle_expense()` 도 재무 원장에 이미 있었고,
    #:   **부르는 자리 하나**가 없었다 — 유지보수·전이 재시도 때와 같은 모양이다.
    #:
    #: ★ **어휘를 새로 만들지 않았다.** `maintenance_status` · `pending_transition_status`
    #:   가 쓰는 넷 그대로다. `SETTLED` 같은 말을 여기서 짓지 않는다 — 지으면 같은
    #:   사실을 부르는 이름이 단계마다 달라진다.
    #:
    #: ```text
    #: NOT_ATTEMPTED   안 켰다 — auto_settle_expenses 가 거짓이었다 · 거기까지 못 갔다
    #:                 · 장부 관문이 막은 날이다
    #: RAN             지급한 건이 있었다 — 몇 건 얼마인지는 expense_settlements 가 말한다
    #: NOTHING_DUE     확인했고 지급할 것이 없었다 — 🟢 정상이다
    #: FAILED          하려다 터졌다 — 🔴 **그날 마감을 BLOCKED 로 막는다**
    #: ```
    #:
    #: 🔴 **`FAILED` 만 앞의 셋과 태도가 다르다.** 유지보수·전이·점검은 터져도 하루가
    #:   계속 가는데 이 칸은 **마감을 막는다**. 지급은 현금을 건드리기 때문이다 —
    #:   그냥 넘기면 그날이 정상 `CLOSED` 로 서고 **«현금은 줄었는데 비용은 0원»** 인
    #:   기록이 손익 곡선의 확정값으로 앉는다.
    #:
    #: 🔴 **`NOT_ATTEMPTED` 와 `NOTHING_DUE` 를 접지 않는다.** 앞은 *"안 켰다"* 이고
    #:   뒤는 *"켰는데 지급일이 된 것이 없었다"* 다 — 지급 0건의 이유가 그 둘로 갈린다.
    expense_settlement_status: str = "NOT_ATTEMPTED"
    #: 마감 단계. 🔴 **하루의 맨 끝이다 — 출고 뒤다.**
    #:
    #: ★ **어휘를 새로 만들지 않았다.** `ClosingOut.status` 의 다섯 값
    #: (`CLOSED` · `NOTHING_DUE` · `BLOCKED` · `NOT_OPENED` · `FAILED`)을 그대로
    #: 싣고, 단계를 안 탄 날은 이 클래스가 이미 쓰는 `NOT_ATTEMPTED` 다.
    #:
    #: 🔴 **`NOT_ATTEMPTED` 와 `BLOCKED` 를 접지 않는다.** 앞은 *"그날을 아예 안
    #: 돌았다"* (휴장·`WAIT`·개장 실패)이고 뒤는 *"돌았는데 장부가 안 서서 못
    #: 닫았다"* 이다. 손익 곡선에는 둘 다 빈 칸으로 보이므로, **이 값이 아니면
    #: 둘을 가를 데가 없다.**
    closing_status: str = "NOT_ATTEMPTED"
    items: tuple[ItemRunOutcome, ...] = ()
    #: 판매 판단의 품목별 결과 (2026-09-10). 🔴 **`items` 와 섞지 않는다.**
    #:
    #: ★ **모양은 같고 축이 다르다.** `ItemRunOutcome` 을 그대로 쓰되 한 칸에 담지
    #:   않는다 — 섞으면 `failed_items` 가 *"어느 사이클이 터졌나"* 를 못 말하고,
    #:   같은 품목이 두 번 앉아 품목 수를 세는 모든 자리가 두 배로 읽힌다.
    #:
    #: ★ `end_code` 는 판매 어휘 그대로다 (`SL1_PRESENTED` 등). 🔴 **매입 어휘로
    #:   접지 않는다** — `backtest_runner` 가 `end_codes` 를 세는 자리와 같은 규율이다.
    sales_items: tuple[ItemRunOutcome, ...] = ()
    #: 매입 판단 **바로 뒤** 자동 승인 단계를 **탔는가** (2026-09-11).
    #:
    #: ★ **어휘를 새로 만들지 않았다.** `NOT_ATTEMPTED` · `FAILED` 는 이 클래스가
    #:   이미 쓰는 말이고, `RAN` · `NO_RULE` 은 `BackfillOut.status` 의 두 값
    #:   그대로다 (`closing_status` 가 `ClosingOut.status` 를 그대로 싣는 것과 같다).
    #:
    #: ```text
    #: NOT_ATTEMPTED   안 켰다 — auto_approve 가 거짓이었다 · 거기까지 못 갔다
    #: RAN             승인 문까지 돌았다 — 몇 건이 적혔는지는 outcomes 가 말한다
    #: NO_RULE         켰는데 그 실행이 규칙을 안 들었다
    #: FAILED          돌리다 터졌다 — 🔴 **그래도 하루는 계속 간다**
    #: NO_ML_BATCH     배치가 원래 없는 날이라 판단도 승인도 안 돌렸다 (2026-09-13)
    #: ```
    #:
    #: ⚠️ **`NO_ML_BATCH` 는 `auto_approve` 를 안 본다.** 그날은 켰든 안 켰든 승인할
    #:   판단이 없고, 안 켠 날에 `NOT_ATTEMPTED` 로 두면 승인 줄에서만 그 날이 사라진다.
    #:
    #: 🔴 **`NOT_ATTEMPTED` 와 `NO_RULE` 을 접지 않는다.** 앞은 *"안 켰다"* 이고
    #:   뒤는 *"켰는데 규칙이 없었다"* 다 — 승인 0건의 이유가 그 둘로 갈린다.
    procurement_approval_status: str = "NOT_ATTEMPTED"
    #: 판매 판단 바로 뒤 자동 승인 단계. 🔴 **매입과 같은 모양·같은 어휘다.**
    sales_approval_status: str = "NOT_ATTEMPTED"
    #: 매입 승인이 낸 값 그대로. 🔴 **여기서 다시 세지 않는다** — 어휘 여덟의
    #: 주인은 `BackfillOut.outcomes` 하나다. 안 켠 날은 `None`.
    #: 미적용 전이 재시도가 낸 값 그대로 (2026-09-11). 🔴 **여기서 다시 세지 않는다** —
    #: 어휘 넷의 주인은 `RetryOut.outcomes` 하나다. 단계를 안 탄 날은 `None`.
    pending_transition: RetryOut | None = None
    #: 유지보수가 낸 값 그대로 (2026-09-11). 🔴 **접지 않는다** — 몇 Lot 을 봤고
    #: 무엇을 버렸고 무엇을 사람에게 남겼는지의 주인은 `AutoMaintenanceResult` 다.
    #: 안 켠 날은 `None`.
    maintenance: MaintenanceOut | None = None
    #: 물류 점검 두 칸이 낸 값 그대로 (2026-09-12). 🔴 **여기서 다시 세지 않는다** —
    #: 연 것 · 갱신 · 닫은 것의 주인은 `DetectOut` 하나다. 칸을 안 탄 날은 `None`.
    #:
    #: ⚠️ **두 칸을 한 칸에 안 담는다.** 입고 뒤는 «무엇이 새로 문제인가» 이고 출고 뒤는
    #:    «무엇이 해결됐나» 라, 섞으면 그날 닫힌 문제가 몇이었는지를 요약이 못 말한다
    #:    (`items` 와 `sales_items` 를 가른 것과 같은 이유).
    inspection_inbound: InspectionOut | None = None
    inspection_outbound: InspectionOut | None = None
    procurement_approval: BackfillOut | None = None
    #: 판매 승인이 낸 값 그대로. ⚠️ **매입 것과 한 칸에 안 담는다** — 섞으면
    #: 어느 사이클의 승인이 안 섰는지를 요약이 못 말한다 (`items` 와 `sales_items`
    #: 를 가른 것과 같은 이유).
    sales_approval: BackfillOut | None = None
    #: 출고가 낸 값 그대로 (2026-09-15 · 물류 문서 24 §5-㉣). 🔴 **여기서 다시 세지
    #: 않는다** — 품목별 결과의 주인은 `OutboundOut.items` 하나이고, 여기는 그것을
    #: 가리킬 뿐이다. 걷기 요약이 FAILED 품목마다 한 줄을 찍으려고 싣는다.
    #: 출고 단계를 안 탔거나 터져서 값이 없으면 `None`.
    outbound: OutboundOut | None = None
    #: 운영비 지급이 낸 값 그대로 (2026-09-17). 🔴 **여기서 다시 세지 않는다** —
    #: 몇 건이 얼마 나갔는지의 주인은 `ExpenseSettlement` 하나이고, 여기는 그것을
    #: 가리킬 뿐이다 (`outbound` · `closing` 과 같은 모양). 걷기 요약이 지급이 있던
    #: 날마다 한 줄을 찍으려고 싣는다.
    #:
    #: ⚠️ **빈 튜플이 두 가지 뜻이 아니다.** *"안 켰다"* 와 *"켰는데 없었다"* 는
    #:   `expense_settlement_status` 가 가른다 — 이 칸은 **낸 값**만 든다.
    expense_settlements: tuple[ExpenseSettlement, ...] = ()
    #: 마감이 낸 값 그대로 (2026-09-16). 🔴 **여기서 사유를 다시 짓지 않는다** — 사유의
    #: 주인은 `ClosingOut.reason` 하나이고, 여기는 그것을 가리킬 뿐이다. 걷기 요약이
    #: 첫 마감 실패의 사유를 찍으려고 싣는다 (`outbound` 와 같은 모양).
    #: 마감 단계를 안 탔거나 예외로 터져서 값이 없으면 `None`.
    closing: ClosingOut | None = None
    #: 단계별 사유. 사람이 읽을 자리다.
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def failed_items(self) -> tuple[str, ...]:
        """터진 품목. **나머지는 계속 돌았다.**

        ⚠️ **매입 축이다.** 판매가 터진 품목은 `failed_sales_items` 가 나른다 —
          한 property 로 합치면 어느 사이클이 터졌는지가 사라진다.
        """
        return tuple(one.item for one in self.items if one.status == "FAILED")

    @property
    def failed_sales_items(self) -> tuple[str, ...]:
        """판매 판단이 터진 품목. **나머지는 계속 돌았다.**"""
        return tuple(one.item for one in self.sales_items if one.status == "FAILED")
