"""매입 의사결정 실행 — 요청을 Flow 로 옮기고 결과를 응답으로 옮긴다.

여기에 판단을 두지 않는다. 판단은 `service/flow.py` 에 있다. 이 모듈은 경계 변환만
한다 — 그래야 API 모양이 바뀌어도 Flow 가 흔들리지 않는다. 판매 쪽 진입점은
`service/sales.py` 다.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.contracts.envelope import ExecutionContext
from app.master.domain.answer import facts_from_procurement, render_answer
from app.master.domain.execution_calendar import build_execution_calendar
from app.master.domain.execution_day import (
    CalendarNotCovered,
    ExecutionDayNotFound,
    HolidayCalendar,
    is_execution_day,
    next_execution_day,
)
from app.master.domain.flow import ProcurementOutcome
from app.master.domain.request_ids import make_request_id
from app.master.domain.run_response import empty_response, to_response
from app.master.domain.sim_run import sim_run_id_of
from app.master.readmodel.approvals import commitments_before
from app.master.readmodel.decisions import list_decisions
from app.master.readmodel.holiday_calendar import get_calendar
from app.master.readmodel.inputs import collect_inputs
from app.master.readmodel.market_calendar import get_market_calendar
from app.master.registry import wiring
from app.master.schemas.decision import CommitmentOut
from app.master.schemas.inputs import DEFAULT_GRADE, REQUEST_GRADE, MasterInputs
from app.master.schemas.procurement import ProcurementRunRequest, ProcurementRunResponse
from app.master.schemas.sales import SalesRunRequest
from app.master.service import persistence
from app.master.service.budget import CallBudget
from app.master.service.day_gate import check_day_gate
from app.master.service.flow import ProcurementFlow, VerifierPort
from app.master.service.runner import MasterRunner
from app.master.service.verifier import MasterVerifier

logger = logging.getLogger(__name__)

# 사유 문장에 요일을 적기 위한 이름. `date.weekday()` 순서 (월 0 … 일 6).
# 로케일을 타지 않게 직접 적는다 — 서버 로케일에 따라 사유 문장이 갈리면 안 된다.
_WEEKDAY_NAMES = ("월", "화", "수", "목", "금", "토", "일")

# `date.weekday()` 가 토요일에 주는 값. 사유 문장이 주말인지 공휴일인지를 가리는 데만
# 쓴다 — 판정은 `execution_day` 가 한다.
_SATURDAY = 5


def run_procurement(
    request: ProcurementRunRequest,
    verifier: VerifierPort | None = None,
) -> ProcurementRunResponse:
    """매입 Flow 를 한 번 돌리고 실행 계획을 적재한다.

    적재는 계산이 끝난 뒤다. 실패해도 응답을 막지 않는다(§persistence).

    `verifier` 를 주지 않으면 마스터의 기본 검증 Tool 이 붙는다(§3.7.1). 기본값이
    `None` 이면 API 경로에서 검증이 통째로 건너뛰어지고, `verification_skipped: true`
    로만 드러나 아무도 보지 않는다. 끄려면 명시적으로 꺼야 한다(`NO_VERIFIER`)는 쪽이
    안전하다.
    """
    verifier = MasterVerifier() if verifier is None else verifier
    started = time.perf_counter()
    request_id = request.request_id or make_request_id(request.as_of.isoformat())
    context = ExecutionContext(
        request_id=request_id,
        as_of=request.as_of,
        trigger=request.trigger,
        policy_version=request.policy_version,
        # 어느 실행의 장부인가는 마스터가 정한다(물류 `#325`). 물류 조회 경로에는
        # 생성자가 없어 봉투 말고 줄 자리가 없다 — `ExecutionContext` docstring 의 ①.
        #
        # 요청이 주면 그 값이 이긴다(`#531`). 여기서 상수를 다시 적으면 걷기가 축을
        # 줘도 축이 봉투 앞에서 끊기고 — 판단 행이 번인으로 앉고, 승인 경로가 그 행을
        # 읽으니 원장까지 번인으로 돌아온다.
        #
        # 기본값을 없애지 않는다. 라우터·화면은 이 칸을 주지 않는다. 대신 기본값으로
        # 떨어진 사실을 `_input_sources` 가 `DEFAULT:burn_in` 으로 적는다 —
        # 조용히 번인에 쌓지 않는다.
        sim_run_id=sim_run_id_of(request),
    )

    # 첫 관문은 개장이다(계약). 실행일 판정보다 먼저다 —
    # 그 날 장부가 안 열렸으면 실행일이어도 읽을 상태가 없다.
    #
    # 두 관문은 다른 물음이다. 토요일은 여기를 통과하고 아래에서 막힌다.
    day_gate = check_day_gate(request.as_of, sim_run_id=context.sim_run_id)
    if day_gate.gate == "BLOCKED":
        response = empty_response(
            context,
            reason=day_gate.reason or "그날 장부가 안 열렸다",
            skipped_note="전 검사: 그날이 안 열려서 Flow 가 시작되지 않음",
        )
        response.day_gate = day_gate
        response.report_text = render_answer(facts_from_procurement(response))
        response.history_run_id = persistence.record(
            request, response, elapsed_ms=elapsed(started), sim_run_id=context.sim_run_id
        )
        return response

    execution_day = _execution_day_verdict(request.as_of)
    if not execution_day.runs:
        # 주말·공휴일은 오류가 아니라 안 도는 날이다 — 어댑터 미등록과 같은 태도다(§5.3).
        # 그날에는 ML 예측이 없다. 없는 값을 복사본으로 채워 판단하면
        # 그건 시장을 본 것이 아니라 금요일을 두 번 본 것이다.
        #
        # 「시장이 안 선다」로 적지 않는다(매입 지적). 토요일은
        # `ml_calendar_days.is_open` 이 참이다 — 실측 34일 참 / 3일 거짓.
        # 시장은 서는데 예측이 없는 것이고, 우리가 안 도는 이유는 뒤쪽이다.
        response = empty_response(
            context,
            reason=_not_execution_day_reason(request.as_of, execution_day.following),
            skipped_note="전 검사: 실행일이 아니어서 Flow 가 시작되지 않음",
        )
        # 개장은 통과했다는 사실을 같이 낸다. 토요일이 여기서 막힐 때 화면이
        # "안 열려서" 와 "장이 안 서서" 를 구분할 수 있어야 한다.
        response.day_gate = day_gate
        # 공휴일 축을 못 봤으면 그 사실도 같이 남긴다 — 접힌 이유가 주말이라도
        # "공휴일까지 봤다" 로 읽히면 안 된다.
        response.skipped_checks = [*response.skipped_checks, *execution_day.skipped]
        # 못 돈 날도 사람이 읽을 수 있어야 한다 — 빈 응답을 그대로 내보내면 화면이 침묵한다
        response.report_text = render_answer(facts_from_procurement(response))
        # 안 돈 날도 이력에 남긴다. 어댑터 갈래와 같은 이유다 —
        # 안 부른 것과 안 도는 날인 것은 다르고, 이력이 비면 둘이 같아 보인다.
        response.history_run_id = persistence.record(
            request, response, elapsed_ms=elapsed(started), sim_run_id=context.sim_run_id
        )
        return response

    missing = wiring.missing()
    if missing:
        # 어댑터 미구현은 오류가 아니라 "그 부서가 오늘 돌지 않는다"와 같다 (§5.3)
        response = empty_response(
            context,
            reason=f"어댑터 미등록: {', '.join(missing)}",
            missing_adapters=list(missing),
        )
        # 못 돈 날도 사람이 읽을 수 있어야 한다 — 빈 응답을 그대로 내보내면 화면이 침묵한다
        response.report_text = render_answer(facts_from_procurement(response))
        # 어댑터가 없어 못 돈 날도 이력에 남긴다 — 안 부른 것과 못 부른 것은 다르다
        response.history_run_id = persistence.record(
            request, response, elapsed_ms=elapsed(started), sim_run_id=context.sim_run_id
        )
        return response

    inputs = _inputs_for(request, sim_run_id=context.sim_run_id)
    commitments = _approved_commitments(request)
    # 달력이 출처표보다 먼저다(`#300`). 출처표가 봉투를 실었는지도 적기 때문에,
    # 순서가 뒤집히면 아직 없는 것을 보고 매번 `MISSING` 이라고 적는다.
    calendar_envelope, calendar_skipped = _execution_calendar_payload(request.as_of)
    # 주입한 키는 주입이라고 적는다(`_input_sources`).
    sources = _input_sources(request, inputs, execution_calendar=calendar_envelope)
    runner = MasterRunner(context, wiring.registry(), CallBudget(limit=request.budget))
    outcome = ProcurementFlow(
        runner,
        verifier=verifier,
        item=request.item,
        forecast=request.forecast or _payload(inputs, "forecast"),
        confirmed_orders=request.confirmed_orders or _payload(inputs, "confirmed_orders"),
        policy_values=request.policy_values or _payload(inputs, "policy_values"),
        prior_feedback=request.prior_feedback,
        approved_commitments=commitments.carried,
        # 달력을 값으로 싣는다. 매입은 봉투만 받는 파트라 `is_execution_day` 를
        # 인용해도 `calendar` 를 못 준다 — 인용하면 주말만 피한다.
        # N4 · N5 와 같은 모양이다: 값은 아는 쪽이, 계산은 쓰는 쪽이.
        execution_calendar=calendar_envelope,
        # 값과 출처를 떼어 놓지 않는다. 응답뿐 아니라 payload 에도 나른다.
        input_sources=sources,
        # 실어 주기만 하지 않고 막는 쪽까지 잇는다.
        # 응답의 `mocked_inputs` 는 화면 경고용이고, 이것은 실행을 세우는 용도다.
        mocked_inputs=inputs.mocked if inputs else (),
    ).run(has_unmet_obligation=request.has_unmet_obligation)

    response = to_response(context, outcome, inputs, sources)
    response.day_gate = day_gate
    response.concerns = [
        *response.concerns,
        *_decision_collision(request_id),
        *_evidence_contract_concerns(outcome),
        # 약정을 못 읽었거나 못 실은 사실(#185). 재호출로 안 고쳐지므로
        # findings 가 아니라 concerns 다.
        *commitments.concerns,
    ]
    # 돈 날에도 못 본 축은 적는다. 공휴일 축이 빠진 채 "실행일이다" 라고 답했으면
    # 그건 "주말이 아니다" 까지만 확인한 것이다 — 비워 두면 다 봤다고 읽힌다
    # (`verifier.py` 의 `skipped` 와 같은 규율).
    #
    # 봉투를 못 실은 것도 같은 자리에 적는다. 문 앞 판정은 통과했는데 지평 어딘가가
    # 안 덮이는 경우가 있다 — 그때 매입은 회차일을 밀지 않는 동작으로 돈다.
    response.skipped_checks = [*response.skipped_checks, *execution_day.skipped, *calendar_skipped]
    response.report_text = render_answer(facts_from_procurement(response))
    # 적재가 돌려준 행 id 를 응답에 싣는다. 화면이 승인할 때 이 값을 되돌려 줘야
    # "내가 본 그것을 승인했다" 가 기록된다(§DDL 안건 2026-08-30).
    response.history_run_id = persistence.record(
        request, response, elapsed_ms=elapsed(started), sim_run_id=context.sim_run_id
    )
    return response


@dataclass(frozen=True)
class _ExecutionDayVerdict:
    """문 앞의 실행일 판정 하나. 본 것과 못 본 것을 같이 든다.

    ```text
    runs       이 날 도는가
    following  안 돈다면 다음은 언제인가 (못 찾으면 None)
    skipped    못 본 축 — 비어 있으면 주말·공휴일을 다 봤다는 뜻이다
    ```
    """

    runs: bool
    following: date | None
    skipped: tuple[str, ...] = ()


def _execution_day_verdict(
    as_of: date, calendar: HolidayCalendar | None = None
) -> _ExecutionDayVerdict:
    """이 날 도는가. 공휴일 축을 붙여서 묻고, 못 붙으면 주말 축만으로 답한다.

    실패 처리: 달력이 죽었다고 매입 판단이 멈추면 안 된다. 공휴일을 못 봐도 주말
    축으로는 판단할 수 있다. 여기서 막으면 ML 쪽 뷰 하나가 마스터 전체의 정지 스위치가
    된다(`#282`). 그렇다고 조용히 넘어가지도 않는다. 못 본 축은 `skipped` 로 나가서
    응답에 남는다 — "안 한 것을 안 했다고 적는다"(`verifier.py`).

    두 물음을 따로 판단한다. "오늘 도는가" 는 달력이 오늘을 덮으면 답이 나오고, "다음은
    언제인가" 는 앞으로 걷다가 달력 밖으로 나갈 수 있다. 뒤가 실패했다고 앞의 답까지
    버리면, 덮이는 날의 공휴일 판정을 덮이지 않는 날 때문에 잃는다.

    뷰가 `CURRENT_DATE` 까지만 나오므로 오늘·미래를 묻는 호출은 달력 밖이다(실측
    2026-09-04: 뷰가 2025-09-04~2026-09-04). 그 경로가 곧 여기다.
    """
    calendar = get_calendar() if calendar is None else calendar
    skipped: list[str] = []

    try:
        runs = is_execution_day(as_of, calendar=calendar)
    except CalendarNotCovered as exc:
        skipped.append(f"공휴일 축: {as_of.isoformat()} 을 판정 못 함 — {exc}. 주말 축만 돌았다")
        runs = is_execution_day(as_of)

    if runs:
        return _ExecutionDayVerdict(True, None, tuple(skipped))

    following, why = _next_execution_day_or_none(as_of, calendar)
    if why:
        skipped.append(why)
    return _ExecutionDayVerdict(False, following, tuple(skipped))


def _next_execution_day_or_none(
    as_of: date, calendar: HolidayCalendar
) -> tuple[date | None, str]:
    """다음 실행일과, 그것을 어떻게 골랐는지. 사유가 비면 공휴일까지 보고 골랐다.

    달력 밖으로 나가면 주말 축만으로 다시 고른다. 사람이 읽는 사유에 "다음에 언제
    도나" 가 없는 것보다, 공휴일을 못 본 채 고른 날짜와 그 사실을 같이 주는 편이 낫다.
    """
    try:
        return next_execution_day(as_of, calendar=calendar), ""
    except CalendarNotCovered as exc:
        why = f"공휴일 축: 다음 실행일을 찾다 달력 밖으로 나갔다 — {exc}. 주말 축만으로 골랐다"
    except ExecutionDayNotFound as exc:
        # 달력은 읽혔는데 상한까지 실행일이 없다 — 달력이 틀렸을 가능성이 크다.
        return None, f"다음 실행일: 못 찾았다 — {exc}"

    try:
        return next_execution_day(as_of), why
    except ExecutionDayNotFound as exc:  # pragma: no cover - 주말은 최대 이틀이다
        return None, f"다음 실행일: 못 찾았다 — {exc}"


def _execution_calendar_payload(as_of: date) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    """매입에 실을 실행일 봉투와, 못 실었으면 그 사유.

    문 앞과 축이 다르다(`#303` · 매입 리뷰).

      ```text
      문 앞     "그날 ML 예측이 있어 판단을 도는가"   holiday_nm + 주말   get_calendar()
      봉투      "그날 시장에서 살 수 있는가"          is_open            get_market_calendar()
      ```

    마스터가 토요일에 안 도는 이유는 "장이 안 서서" 가 아니라 예측이 없어서다. 2026년
    토요일 45일에 가락이 서고, 그 45일을 문 앞 축으로 밀면 살 수 있는 날에 못 산다고
    계획한다.

    묻는 범위도 다르다. 문 앞은 하루를 묻고 봉투는 지평 전체를 묻는다 — 오늘은
    덮이는데 지평 끝이 안 덮이는 날이 있다.

    못 덮으면 봉투를 통째로 싣지 않는다. 덮인 데까지만 실으면 지평이 거짓말을 한다 —
    받는 쪽은 `horizon_end` 까지 다 봤다고 읽는다. 반쪽 달력보다 없는 달력이 낫다:
    없으면 매입이 회차일을 밀지 않는 동작으로 돌고, 그 사실이 `skipped_checks` 에
    남는다.

    실패 처리: 달력이 죽었다고 매입 판단을 멈추지 않는다. 멈추면 시뮬레이션 전체가
    선다. `execution_day.py` 가 정한 태도 그대로다 — "부르는 쪽이 정한다."
    """
    try:
        envelope = build_execution_calendar(as_of, market=get_market_calendar())
    except CalendarNotCovered as exc:
        return None, (
            (
                f"실행일 봉투: {as_of.isoformat()} 부터의 지평을 달력이 다 안 덮는다 — {exc}."
                " 매입에 비영업일 목록을 안 실었다 (매입은 회차일을 밀지 않는다)"
            ),
        )
    return envelope.as_payload(), ()


def _not_execution_day_reason(as_of: date, following: date | None) -> str:
    """안 도는 날의 사유. 왜 안 도는지와 언제 다시 도는지를 같이 적는다.

    주말과 공휴일을 구분해 적는다. 설날을 "주말이라" 로 적으면 사유가 거짓말을 한다 —
    사람이 달력을 다시 보게 된다.

    판정은 여기서 하지 않는다. `execution_day` 가 "안 도는 날" 이라고 했고, 평일인데 안
    도는 날은 공휴일뿐이다.
    """
    label = "주말" if as_of.weekday() >= _SATURDAY else "공휴일"
    head = (
        f"실행일이 아니다: {as_of.isoformat()}"
        f"({_WEEKDAY_NAMES[as_of.weekday()]})은 {label}이라 ML 예측이 없다. "
    )
    if following is None:
        # 못 찾은 것을 지어내지 않는다. 사유에 날짜가 없는 것이 사실이다.
        middle = "다음 실행일은 찾지 못했다. "
    else:
        middle = (
            f"다음 실행일은 {following.isoformat()}"
            f"({_WEEKDAY_NAMES[following.weekday()]})이다. "
        )
    tail = "경과일수는 달력일 그대로 센다 — 안 도는 날이 사라지는 것이 아니다."
    return head + middle + tail


def _decision_collision(request_id: str) -> list[str]:
    """이미 결정이 붙은 업무 키로 다시 도는가.

    `master_agent_runs` 는 append-only 라 같은 키로 두 번 돌면 행이 둘이 되고,
    `get_run_by_request_id` 는 최신 1건을 돌려준다(그게 맞는 동작이다 — "그 요청 어떻게
    됐냐" 에는 마지막 결과가 답이다).

    문제는 결정과 결합할 때 생긴다.

    ```text
    06:22  실행 A (안 3개)
    06:22  '기본' 승인          ← A 의 '기본' 을 골랐다
    06:23  실행 B (같은 키)      ← 이제 조회하면 B 가 나온다
           → 화면에는 "B 의 기본을 승인했다" 로 보인다
    ```

    두 실행의 같은 라벨이 다른 수량이면 승인한 것과 다른 것이 승인된 것으로 읽힌다.
    실측(2026-08-29 리허설)에서 재현했다.

    막지 않고 드러낸다. 재실행 자체는 죄가 아니고, 승인 게이트를 마스터가 들고 있으면
    안 된다(8/26 회의). 사람이 보고 판단할 일이다.

    결정은 `master_decisions.run_id` 로 실행 행을 가리키므로 "그 결정이 이 실행을
    가리키는 것처럼 보인다" 는 사실이 아니다. 그래도 경고는 남긴다 — 이력 조회가 그
    키의 최신 실행 하나를 주기 때문에, 같은 키로 다시 돌리면 앞 결정이 보고 있던 계획이
    화면에서 사라진다. 문구는 그 위험을 가리킨다.

    2026-08-30 이전 결정은 `history_run_id` 가 `None` 이다 — 그때는 정말로 어느
    실행인지 모른다. 그 경우는 "그 결정이 이 실행을 가리키는 것처럼 보인다" 는 문구가
    맞다.
    """
    try:
        existing = list_decisions(request_id)
    except Exception:  # noqa: BLE001 — 경고를 못 만든다고 실행을 막지 않는다
        return []
    if not existing:
        return []
    current = next((row for row in existing if row.is_current), existing[-1])
    label = f" · {current.scenario_label}" if current.scenario_label else ""
    head = (
        f"DECISION-COLLISION: 이 업무 키에는 이미 결정이 있다 "
        f"({current.decision_seq}회차 {current.decision}{label}). "
    )
    if current.history_run_id:
        tail = (
            f"그 결정은 **다른 실행**({current.history_run_id[:8]}…)을 가리키므로 이 "
            "실행이 승인된 것은 아니다. 다만 이력 조회는 최신 실행 하나만 보여주므로 "
            "앞 결정이 보고 있던 계획이 화면에서 사라진다 — 조건을 바꿔 다시 만들려면 "
            "새 업무 키를 써라"
        )
    else:
        # 2026-08-30 이전 결정 — 정말로 어느 실행인지 모른다
        tail = (
            "그 결정은 **어느 실행을 가리키는지 기록되지 않았다** (2026-08-30 이전). "
            "같은 키로 다시 돌면 그 결정이 이 실행을 가리키는 것처럼 보인다 — "
            "조건을 바꿔 다시 만들려면 새 업무 키를 써라"
        )
    return [head + tail]


def _inputs_for(request: ProcurementRunRequest, *, sim_run_id: str) -> MasterInputs | None:
    """마스터가 실어 줄 셋을 모은다 (§3.2.5).

    `sim_run_id` 는 봉투(`context.sim_run_id`)에서 받는다. 새로 짓지 않고, 기본값도
    두지 않는다 — 확정 주문이 이 실행의 판매만 읽게 하는 축이다.

    요청이 직접 준 값이 이긴다. 백테스트는 그날의 값을 그대로 넣어야 하므로 적재층이
    현재 DB 를 읽어 덮으면 안 된다.

    품목이 없으면 모으지 않는다 — 셋 다 품목 단위다. 매입이 `missing_data: ["item"]`
    으로 답하는 것이 정상 경로다.

    실패 처리: 적재 실패가 Flow 를 막지 않는다. 못 실으면 매입이 `missing_data` 로
    답하고 `E4` 가 된다 — 그것도 사실의 기록이다.
    """
    if not request.item:
        return None
    if request.forecast and request.confirmed_orders and request.policy_values:
        return None
    try:
        return collect_inputs(request.item, request.as_of, sim_run_id=sim_run_id)
    except Exception:  # noqa: BLE001
        return None


#: 요청 본문이 직접 실을 수 있는 입력 3종 (§3.2.5 의 백테스트 통로).
#: `run_procurement` 이 `request.<key> or _payload(inputs, key)` 로 쓰는 그 셋이고,
#: 이름이 갈리면 출처표가 값과 어긋나므로 한 자리에서만 적는다.
_INJECTABLE_INPUTS: tuple[str, ...] = ("forecast", "confirmed_orders", "policy_values")

#: 실행일 봉투가 출처표에서 쓰는 이름과 소스. `AgentRequest.payload` 의 키와 같은
#: 이름을 쓴다 — 받는 쪽이 표와 봉투를 한 이름으로 맞춰 읽는다.
_CALENDAR_KEY = "execution_calendar"
_CALENDAR_SOURCE = "market_calendar"

#: 실행 축이 출처표에서 쓰는 이름과 기본값 소스. `_CALENDAR_KEY` 와 같은 모양이다 —
#: 요청 칸 이름(`ProcurementRunRequest.sim_run_id`)을 그대로 쓴다.
_SIM_RUN_KEY = "sim_run_id"
_SIM_RUN_DEFAULT_SOURCE = "burn_in"


def _sim_run_source(request: ProcurementRunRequest | SalesRunRequest) -> str:
    """실행 축의 출처 한 줄. 값과 같은 판정을 쓴다(`sim_run_id_of`).

    ```text
    REQUEST:sim_run_id   요청이 줬다
    DEFAULT:burn_in      요청이 안 줘서 마스터 기본값으로 떨어졌다
    ```
    """
    given = (request.sim_run_id or "").strip()
    if given:
        return f"{REQUEST_GRADE}:{_SIM_RUN_KEY}"
    return f"{DEFAULT_GRADE}:{_SIM_RUN_DEFAULT_SOURCE}"


def _input_sources(
    request: ProcurementRunRequest,
    inputs: MasterInputs | None,
    *,
    execution_calendar: dict[str, Any] | None = None,
) -> dict[str, str]:
    """이번 실행이 실제로 쓴 값의 출처표.

    주입은 mock 이 아니고 측정도 아니다(매입 실측 2026-09-07). 백테스트 통로는
    정당하다 — "요청이 직접 준 값이 이긴다" 는 `_inputs_for` 의 계약이다. 다만 그 사실이
    화면까지 가야 한다. 주입한 키를 적지 않으면 셋 다 주입한 날 `input_sources` 가 비어
    경고가 0건이고, forecast 만 주입한 날은 `MEASURED:v_ml_price_forecast` 로 남아 실제로
    쓴 주입분과 다른 출처를 말한다.

    빈 것은 "모른다" 이지만 틀린 출처는 "안다고 잘못 말하는 것" 이다. 뒤가 나쁘다.

    `mocked_inputs` 와 섞지 않는다. 그것은 `grade == "MOCK"` 만 세고, 세면
    `ProcurementFlow` 가 실행을 세운다. 주입은 세울 일이 아니라 적을 일이다 — 한 칸에
    두 사실을 담으면 둘 다 못 읽는다.

    ```text
    REQUEST:<key>      요청 본문이 준 값 — 이번 실행이 실제로 쓴 것
    MEASURED:<source>  DB 에서 읽은 값
    DERIVED:<source>   DB 값에서 규칙으로 파생
    MISSING:- · MOCK:  (`inputs.py` 그대로)
    ```

    실행일 봉투도 여기 적는다(`#300`). 봉투는 `AgentRequest.payload` 에만 실리고
    `master_agent_runs` 는 그것을 담지 않는다. 출처표에 적지 않으면 받는 쪽은 확인할
    표가 없어 없는 표를 뒤진다(매입 보고 2026-09-10: 665건 전수 0건으로 읽었는데,
    실제로는 655건 중 `CalendarNotCovered` 0건, 즉 매번 실렸다).

      .. code-block:: text

          실었다      execution_calendar: "DERIVED:market_calendar"
          못 실었다   execution_calendar: "MISSING:-"

    어휘를 새로 만들지 않는다. 봉투는 DB 시장달력에서 규칙으로 파생하므로 위 다섯 중
    `DERIVED` 다.

    못 실었을 때도 키를 적는다. 키를 통째로 빼면 "안 실렸다" 가 "모른다" 와 섞인다 —
    없는 표를 다시 뒤지게 된다.

    실행 축도 여기 적는다(`#531`). 요청이 `sim_run_id` 를 주지 않으면 마스터가 번인
    상수로 떨어뜨린다. 그 자체는 계약이지만, 떨어졌다는 사실이 아무 데도 안 적히면 "말
    안 하고 번인에 쌓는" 길이 남는다 — 나중에 읽는 사람이 그 판단 행이 누가 고른 실행에
    앉았는지 알 수 없다.

      .. code-block:: text

          요청이 줬다     sim_run_id: "REQUEST:sim_run_id"
          기본값이다      sim_run_id: "DEFAULT:burn_in"

    여기서도 키를 빼지 않는다. 봉투와 같은 이유다 — 「기본값이었다」와 「모른다」가
    섞이면 다시 코드를 뒤지게 된다.

    등급 어휘의 주인은 `schemas/inputs.py` 다(`REQUEST_GRADE` · `DEFAULT_GRADE`) — 값의
    주인이 한 자리여야 화면과 부서 payload 가 한 표를 읽는다.
    """
    sources = dict(inputs.sources()) if inputs else {}
    for key in _INJECTABLE_INPUTS:
        if getattr(request, key, None):
            sources[key] = f"{REQUEST_GRADE}:{key}"
    sources[_CALENDAR_KEY] = f"DERIVED:{_CALENDAR_SOURCE}" if execution_calendar else "MISSING:-"
    sources[_SIM_RUN_KEY] = _sim_run_source(request)
    return sources


@dataclass(frozen=True)
class _CommitmentLookup:
    """어제까지의 약정 조회 결과. 실은 것과 못 실은 이유를 같이 든다(#185).

    `list[dict]` 하나로 돌려주면 아래 셋이 전부 빈 목록이 된다.

    ```text
    승인이 없었다        정상
    조회가 깨졌다        사고
    승인은 있는데 못 만들었다  사고
    ```

    받는 쪽에서 셋이 구별되지 않으면 "어제 승인이 없었나 보다" 로 읽힌다. §1.2-10 의
    "0 과 모름은 다르다" 가 그대로 걸리는 자리다.
    """

    carried: list[dict[str, Any]] = field(default_factory=list)
    concerns: tuple[str, ...] = ()


def _approved_commitments(request: ProcurementRunRequest) -> _CommitmentLookup:
    """어제까지 승인된 확정 입고 약정 (#185).

    못 읽는 것을 없는 것으로 만들지 않는다. 이력 DB 는 없어도 Flow 가 도는 것이
    계약이라(`history_enabled`) 예외를 올리지는 않는다. 대신 못 읽었다는 사실을
    `concerns` 로 응답에 남긴다 — 조용히 비우면 어제 승인이 없었던 것과 같아진다.

    재호출로 안 고쳐지므로 `findings` 가 아니다. 매입을 몇 번 다시 불러도 DB 가 안
    읽히는 사실은 그대로다. 사람이 볼 것이지 재시도할 것이 아니다.

    품목이 없으면 묻지 않는다. 약정은 품목별이라 물을 대상이 없다.
    """
    if not request.item:
        return _CommitmentLookup()
    try:
        found = commitments_before(request.item, request.as_of)
    except Exception as exc:
        # 이력 조회 실패가 매입 실행을 막지 않는다. 다만 조용히 넘어가지도 않는다.
        logger.exception("어제까지의 승인 약정 조회 실패 - 실행은 그대로 돈다")
        note = (
            f"어제까지 승인된 확정 입고 약정을 못 읽었다 (조회 실패: {type(exc).__name__})"
            " - 이번 실행은 어제 승인분을 모른 채 돌았다. '승인이 없었다' 와 다르다."
        )
        return _CommitmentLookup(concerns=(note,))
    return _CommitmentLookup(
        [c.model_dump(mode="json") for c in found if c.buildable],
        tuple(_commitment_gap(c) for c in found if _commitment_gap(c)),
    )


def _commitment_gap(commitment: CommitmentOut) -> str:
    """이 약정이 온전히 실렸는가. 아니면 그 사유. 온전하면 빈 문자열이다.

    두 갈래를 다 본다.

    ```text
    buildable=False              아예 안 실린다
    buildable + 빈 arrival_schedule  실리는데 도착 일정이 없다
    ```

    뒤가 더 위험하다 - 물류가 받기는 받고 "입고 예정이 없다" 로 읽는다.
    `CommitmentOut.notes` 가 그 사유를 들고 있으므로 그것을 꺼내 적는다.
    """
    label = commitment.approval_id or commitment.scenario_label or "승인분"
    if not commitment.buildable:
        return (
            f"{label}: 어제 승인된 매입의 약정을 못 만들어 경계 호출에 안 실었다"
            f" - {commitment.reason}"
        )
    if not commitment.arrival_schedule:
        note = " / ".join(commitment.notes) or "사유 없음"
        return f"{label}: 약정은 섰는데 도착 일정이 비었다 - {note}"
    return ""


def _payload(inputs: MasterInputs | None, key: str):
    if inputs is None:
        return None
    sourced = getattr(inputs, key)
    return sourced.payload if sourced.usable else None


def elapsed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _evidence_contract_concerns(outcome: ProcurementOutcome) -> list[str]:
    """근거의 값이 계약과 다른가.

    `contracts_core.Evidence.value` 는 `float` 인데 `Evidence` 가 dataclass 라
    런타임 검증이 없다. 그래서 문자열을 넣어도 아무 데서도 안 걸리고, 실제로
    재무 `policy_version_used` 가 `"v1.3-PROVISIONAL"` 을 싣고 있다
    (`finance/service/capabilities/procurement.py`).

    값을 고치거나 버리지 않는다. 고치면 남의 값을 덮어쓰는 것이고(§3.2.2), 버리면
    근거를 고르는 것이다. 원본은 그대로 나가고 여기서 사실만 적는다.

    `findings` 가 아니라 `concerns` 다. 매입을 다시 불러도 안 고쳐진다 - 남의 계약
    문제라 사람이 봐야 한다(§3.4). 다른 부서 회신의 봉투 위반 때문에 매입을 재호출하면
    예산만 쓴다.

    주의: 이 검사는 증상만 잡는다. 어느 쪽이 맞는지는 정하지 않는다 - 계약을
    넓힐지(문자열 근거를 허용) 재무가 다른 칸을 쓸지는 팀이 정할 일이다.
    """
    offenders = [
        f"{item.agent}:{item.evidence.claim}={item.evidence.value!r}"
        for item in outcome.evidences
        if not isinstance(item.evidence.value, (int, float))
        or isinstance(item.evidence.value, bool)
    ]
    if not offenders:
        return []
    message = (
        "EVIDENCE-VALUE-NOT-NUMERIC: 근거의 value 가 계약(float)과 다른 것이 "
        f"{len(offenders)}건이다 - {', '.join(offenders)}. 값은 그대로 나갔고 "
        "마스터가 고치지 않았다. 계약을 넓힐지 부서가 다른 칸을 쓸지는 팀이 정한다."
    )
    return [message]
