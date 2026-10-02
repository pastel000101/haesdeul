"""발화문 입구 — 분류하고, 확인이 필요 없을 때만 실행하고, 사람 말로 답한다.

```text
발화문 → [LLM ①분류] → 확인 필요? ─예→ 되묻고 끝 (아무것도 안 돈다)
                                 └아니오→ 조회 실행 → [LLM ⑥문장] → 답
```

LLM 이 둘이고 역할이 다르다. ①이 실패하면 되물어야 하지만(분류를 못 하면 실행할 수
없다), ⑥이 실패해도 답은 나간다 — 숫자는 규칙이 만들고 LLM 은 앞머리 문장만 얹기
때문이다(`domain/answer.py`).

`flow.py` 는 이 모듈을 모른다. 발화문 해석은 Flow 바깥 일이고, Flow 는 타입이 붙은
요청만 받는다 — 그래야 백테스트에서 Flow 를 그대로 돌릴 수 있다.

확인 없이 실행하는 것은 조회뿐이다. 매입 실행(`PROCUREMENT_RUN`)은 확인을 받은 뒤
`/master/ask/execute` 로 온다. 오분류 비용이 비대칭이기 때문이다 — 조회를 잘못 고르면
다시 물으면 그만이지만, 매입은 예산 12회와 매입 LLM 을 태운다.

조회도 실행이력(`master_agent_runs`)에 적재한다. 조회는 안을 만들지 않지만 예산을 쓰고
부서를 부른다. 안 남기면 그 호출이 이력에서 사라지고, M-16(실행 계획 온전성)이 막으려는
것이 바로 "안 보이는 호출" 이다. 조회만 계속 돌린 날과 아무것도 안 한 날이 같아 보이면
안 된다.

주의: 조회와 매입이 같은 업무 키를 쓴다. 둘 다 `make_request_id(as_of)` 로
`REQ-20251231-0001` 을 만든다 — 순번 관리가 호출자 몫이라 화면이 안 주면 같아진다.
그래서 읽는 쪽이 `cycle` 을 밝힌다(`readmodel/runs.py` 의
`get_run_by_request_id(..., cycle=...)`). 안 밝히면 조회가 최신 행이 되는 날 결정이 조회를
가리키고 이력 화면이 조회를 보여준다. 조회는 승인 대상이 아니다.

발화문 파싱은 `domain/ask_parsers.py`, 부서 조회·쓰기 행동은 `service/ask_domain_actions.py`
에 있다.
"""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import date

from app.contracts.envelope import ExecutionContext
from app.core.settings import SHOWN_SIM_RUN_ID
from app.master.domain.answer import (
    AnswerFacts,
    Fact,
    facts_from_decision,
    facts_from_procurement,
    facts_from_status,
    render_answer,
)
from app.master.domain.ask_parsers import (
    DomainClarification,
    has_report_period,
    missing_domain_slots,
    missing_message,
    slots_of,
)
from app.master.domain.request_ids import make_request_id
from app.master.domain.status_flow import StatusOutcome
from app.master.llm.answer_runtime import NarrativeService, get_narrative_service
from app.master.llm.runtime import IntentService, get_intent_service
from app.master.llm.schemas import Intent, IntentResult
from app.master.readmodel.history import get_run_history
from app.master.registry import wiring
from app.master.schemas.ask import (
    AnswerOut,
    AskExecuteRequest,
    AskRequest,
    AskResponse,
    DomainActionAnswer,
    StatusAnswer,
)
from app.master.schemas.decision import DecisionIn, DecisionRejected
from app.master.schemas.procurement import ProcurementRunRequest, ProcurementRunResponse
from app.master.service import persistence
from app.master.service.ask_domain_actions import (
    DOMAIN_READ_ACTIONS,
    DOMAIN_WRITE_ACTIONS,
    domain_preview,
    run_domain_action,
)
from app.master.service.budget import CallBudget
from app.master.service.decision import link_follow_up, record_decision
from app.master.service.procurement import run_procurement
from app.master.service.runner import MasterRunner
from app.master.service.status_flow import StatusFlow

#: 확인 없이 바로 도는 종류. 조회뿐이다.
_AUTO_RUN = frozenset({"STATUS_QUERY"})


#: 화면이 직접 고른 날짜 범위(`date_from`/`date_to`)를 슬롯으로 받는 보고서.
#:
#: 집합의 주인을 이 상수 하나로 둔다. `ask()` 안에 문자열 집합을 따로 박으면 보고서가
#: 늘 때마다 그 자리를 찾아 고쳐야 하고, 하나가 빠지면 화면에서 고른 기간이 그 보고서에만
#: 먹지 않는다.
#:
#: 주의: 기간 누락 되묻기(`has_report_period`)와는 다른 집합이다. 그쪽은 아직
#: `FINANCE_REPORT_GENERATE` 한 곳에만 걸려 있고(판매도 빠져 있다), 공용 규칙으로
#: 넓힐지는 별도 결정이라 여기서 같이 묶지 않는다.
_REPORT_DATE_RANGE_ACTIONS = frozenset(
    {"FINANCE_REPORT_GENERATE", "SALES_REPORT_GENERATE", "LOGISTICS_REPORT_GENERATE"}
)


def _domain_answer_response(
    *, request_id: str, as_of: date, intent: Intent, result: DomainActionAnswer, outcome: str
) -> AskResponse:
    return AskResponse(
        request_id=request_id,
        as_of=as_of,
        outcome=outcome,  # type: ignore[arg-type]
        intent=intent,
        answer=AnswerOut(text=result.text, markdown=result.markdown, llm_status="SKIPPED_TEMPLATE"),
        domain_result=result,
        llm_status="SKIPPED_TEMPLATE",
        note=_shown_note(as_of),
    )


def _ask_domain_action(
    *, request_id: str, request: AskRequest, result: IntentResult
) -> AskResponse:
    intent = result.intent
    if intent.domain_action == "FINANCE_REPORT_GENERATE" and not has_report_period(intent):
        return _response(
            request_id,
            request,
            result,
            outcome="NEEDS_CLARIFICATION",
            clarification="어느 기간의 재무 보고서를 생성할까요?",
            note="보고 기간이 정해지기 전에는 재무 보고서를 생성하지 않았다.",
        )
    missing = missing_domain_slots(intent)
    if missing:
        return _response(
            request_id,
            request,
            result,
            outcome="NEEDS_CLARIFICATION",
            clarification=missing_message(missing),
            note="필수 정보가 없어 실행하지 않았다.",
        )
    if intent.confidence != "HIGH":
        return _response(
            request_id,
            request,
            result,
            outcome="NEEDS_CLARIFICATION",
            clarification=(
                "요청을 한 가지 의미로 확정하지 못했습니다. 조금 더 구체적으로 말씀해 주세요."
            ),
            note="낮은 분류 신뢰도로 실행하지 않았다.",
        )
    try:
        if intent.domain_action in DOMAIN_READ_ACTIONS:
            domain = run_domain_action(
                intent,
                as_of=request.as_of,
                policy_version=request.policy_version,
                request_id=request_id,
                sim_run_id=request.sim_run_id,
                utterance=request.utterance,
            )
            assert isinstance(domain, DomainActionAnswer)
            response = _domain_answer_response(
                request_id=request_id,
                as_of=request.as_of,
                intent=intent,
                result=domain,
                outcome="DOMAIN_ACTION_ANSWERED",
            )
            # ① 분류의 상태는 보존한다. ⑥ narrative는 부르지 않았다.
            response.llm_status = result.llm_status
            response.llm_provider = result.llm_provider
            response.llm_model = result.llm_model
            response.llm_attempts = result.llm_attempts
            response.llm_fallback_used = result.llm_fallback_used
            return response

        if intent.domain_action in DOMAIN_WRITE_ACTIONS:
            return _response(
                request_id,
                request,
                result,
                outcome="CLASSIFIED_ONLY",
                confirm_required=True,
                clarification=domain_preview(intent, as_of=request.as_of),
                note="확인 전에는 장부를 바꾸지 않았다.",
            )
    except DomainClarification as error:
        return _response(
            request_id,
            request,
            result,
            outcome="NEEDS_CLARIFICATION",
            clarification=str(error),
            note="정보가 하나로 정해지지 않아 실행하지 않았다.",
        )
    raise NotImplementedError(f"{intent.domain_action} DOMAIN_ACTION 경로가 배선되지 않았다.")


def ask(
    request: AskRequest,
    service: IntentService | None = None,
    narrator: NarrativeService | None = None,
) -> AskResponse:
    """발화문을 분류하고, 확인이 필요 없으면 조회까지 돌린 뒤 사람 말로 답한다.

    `service`(①분류) · `narrator`(⑥응답)를 주지 않으면 `.env` 설정으로 만든다.
    테스트가 갈아 끼운다. 둘을 나눠 받는 이유는 역할마다 모델 등급이 달라질 수 있기
    때문이다 — 분류는 소형이면 되고, 응답 문장도 마찬가지지만 판정 검증은 아니다.
    """
    service = service or get_intent_service()
    request_id = request.request_id or make_request_id(request.as_of.isoformat())
    result = service.classify(request.utterance)
    if request.date_from or request.date_to:
        if request.date_from is None or request.date_to is None:
            raise ValueError("시작일과 종료일을 모두 선택해 주세요.")
        if request.date_from > request.date_to:
            raise ValueError("시작일은 종료일보다 늦을 수 없습니다.")
        intent = result.intent
        if intent.domain_action in _REPORT_DATE_RANGE_ACTIONS:
            slots = slots_of(intent).model_copy(update={
                "start_date": request.date_from.isoformat(),
                "end_date": request.date_to.isoformat(),
            })
            updated_intent = intent.model_copy(update={"slots": slots})
            result = result.model_copy(update={"intent": updated_intent})
    intent = result.intent

    if intent.action == "UNKNOWN":
        return _response(
            request_id,
            request,
            result,
            outcome="NEEDS_CLARIFICATION",
            note="발화문을 분류하지 못했다. 실행하지 않았다.",
        )

    if intent.action == "DOMAIN_ACTION":
        return _ask_domain_action(request_id=request_id, request=request, result=result)

    if result.needs_confirmation or intent.action not in _AUTO_RUN:
        return _response(
            request_id,
            request,
            result,
            outcome="CLASSIFIED_ONLY",
            confirm_required=True,
            note="확인 후 /master/ask/execute 로 같은 intent 를 보내면 실행한다.",
        )

    outcome = _run_status(
        request_id=request_id,
        as_of=request.as_of,
        policy_version=request.policy_version,
        budget=request.budget,
        intent=intent,
        question=request.utterance,
    )
    return _response(
        request_id,
        request,
        result,
        outcome="STATUS_ANSWERED",
        status=_to_answer(outcome),
        answer=_write_answer(facts_from_status(outcome), narrator),
        note=_shown_note(request.as_of),
    )


def execute(
    request: AskExecuteRequest,
    narrator: NarrativeService | None = None,
) -> AskResponse | ProcurementRunResponse:
    """사용자가 확인한 의도를 실행한다.

    발화문을 다시 분류하지 않는다. 사용자가 본 의도를 실행한다.

    매입 실행은 `run_procurement` 을 그대로 탄다 — 발화문 경로라고 다른 Flow 를 두면
    두 경로가 조용히 갈라진다.
    """
    intent = request.intent
    request_id = request.request_id or make_request_id(request.as_of.isoformat())

    if intent.action == "DOMAIN_ACTION":
        missing = missing_domain_slots(intent)
        if missing:
            raise DecisionRejected(missing_message(missing))
        try:
            domain = run_domain_action(
                intent,
                as_of=request.as_of,
                policy_version=request.policy_version,
                request_id=request_id,
                actor=request.actor,
                utterance=request.utterance,
            )
        except DomainClarification as error:
            raise DecisionRejected(str(error)) from error
        if isinstance(domain, ProcurementRunResponse):
            return domain
        return _domain_answer_response(
            request_id=request_id,
            as_of=request.as_of,
            intent=intent,
            result=domain,
            outcome=(
                "DOMAIN_ACTION_EXECUTED"
                if intent.domain_action in DOMAIN_WRITE_ACTIONS
                else "DOMAIN_ACTION_ANSWERED"
            ),
        )

    if intent.action == "STATUS_QUERY":
        outcome = _run_status(
            request_id=request_id,
            as_of=request.as_of,
            policy_version=request.policy_version,
            budget=request.budget,
            intent=intent,
            # 확인을 거친 조회는 발화문이 없다 — 화면이 원문을 되돌려 줄 때만 싣는다.
            question=request.utterance,
        )
        return AskResponse(
            request_id=request_id,
            as_of=request.as_of,
            outcome="STATUS_ANSWERED",
            intent=intent,
            status=_to_answer(outcome),
            answer=_write_answer(facts_from_status(outcome), narrator),
            # ①은 안 부른다 (이미 분류된 의도다). ⑥의 상태는 answer 안에 있다.
            llm_status="SKIPPED_TEMPLATE",
            note=_shown_note(request.as_of),
        )

    if intent.action == "PROCUREMENT_RUN":
        response = run_procurement(
            ProcurementRunRequest(
                as_of=request.as_of,
                policy_version=request.policy_version,
                request_id=request_id,
                item=intent.item,
                budget=request.budget,
                # 화면이 보는 실행으로 판단한다. 안 실으면 번인으로 떨어진다
                # (`domain/sim_run.py` 의 `sim_run_id_of`: `given or BURN_IN_SIM_RUN_ID`).
                sim_run_id=SHOWN_SIM_RUN_ID,
            )
        )
        # 여기에는 ⑥ 을 붙이지 않는다. 매입 리포트의 머리말은 이미 완결된 판단
        # 문장이라(`"매입안을 제시합니다. 고르시면 진행합니다."`) LLM 이 얹으면 같은 말을
        # 두 번 한다(실측에서 "매입안을 제시합니다." 가 그대로 중복됐다). 더할 것이 없는
        # 자리에 모델을 부르면 비용과 위험만 는다.
        #
        # 조회는 다르다 — 거기 머리말은 문장이 아니라 머리글이라 얹을 자리가 있다.
        return response

    if intent.action == "SELECT_SCENARIO":
        return _record_selection(request)

    if intent.action == "RERUN_WITH_CONDITION":
        return _record_rerun(request)

    if intent.action == "UNKNOWN":
        # "아직 안 만들었다" 가 아니라 "실행할 것이 없다" 다. 501 로 답하면 언젠가 되는
        # 것처럼 읽힌다 — `UNKNOWN` 은 분류에 실패했다는 뜻이라 영영 실행되지 않는다.
        raise DecisionRejected(
            "UNKNOWN 은 실행할 수 없다 — 무엇을 할지 정해지지 않았다. 다시 물어라."
        )

    # 종류가 늘어났는데 여기 연결을 안 한 경우. 조용히 통과시키지 않는다.
    raise NotImplementedError(f"{intent.action} 실행 경로가 배선되지 않았다.")


# ── 내부 ────────────────────────────────────────────────────────────────


def _run_status(
    *,
    request_id: str,
    as_of: date,
    policy_version: str,
    budget: int,
    intent: Intent,
    question: str | None = None,
) -> StatusOutcome:
    """조회 Flow 를 돌린다. 어댑터 미등록도 결과로 접는다.

    끝에서 이력에 적재한다. 조회는 안을 만들지 않지만 예산을 쓰고 부서를 부른다 — 안
    남기면 그 호출이 이력에서 사라진다. 적재 실패는 답을 막지 않는다(`try_save_run` 이
    삼킨다).
    """
    started = time.perf_counter()
    context = ExecutionContext(
        request_id=request_id,
        as_of=as_of,
        trigger="USER_REQUEST",
        policy_version=policy_version,
        # 조회는 화면이 보는 실행을 읽는다. 번인 상수를 읽으면 2025-12 한 달치 장부만
        # 읽혀, 2026 날짜는 기준일을 바꿔도 늘 같은 물려받은 상태가 나온다. 화면 탭과 같은
        # 한 자리(`app/core/settings.py` 의 `SHOWN_SIM_RUN_ID`)를 가리킨다.
        sim_run_id=SHOWN_SIM_RUN_ID,
    )
    asked = tuple(intent.agents)
    missing = set(wiring.missing())
    registered = tuple(a for a in asked if a not in missing)

    runner = MasterRunner(context, wiring.registry(), CallBudget(limit=budget))
    # 발화 원문은 ML 과 물류가 받고, 품목은 ML 에만 실린다
    # (`StatusFlow._payload_for` · 물류는 원문을 직접 읽어 품목을 푼다).
    outcome = StatusFlow(runner, registered, question=question, item=intent.item).run()

    unregistered = tuple(a for a in asked if a in missing)
    if unregistered:
        # 미등록은 오류가 아니라 "그 부서가 오늘 돌지 않는다"와 같다 (§5.3).
        merged_missing = dict(outcome.missing_data)
        for agent in unregistered:
            merged_missing[agent] = ("ADAPTER_NOT_REGISTERED",)
        answered = len(outcome.answers)
        outcome = StatusOutcome(
            status_code="S3_UNAVAILABLE" if answered == 0 else "S2_PARTIAL",
            reason=(
                f"{', '.join(unregistered)} 어댑터가 등록되지 않았다."
                if answered == 0
                else f"{outcome.reason} {', '.join(unregistered)} 는 어댑터 미등록이다."
            ),
            plan=outcome.plan,
            answers=outcome.answers,
            unavailable=outcome.unavailable + unregistered,
            missing_data=merged_missing,
            errors=outcome.errors,
        )

    # 미등록으로 접힌 결과를 적재한다. 위에서 바로 돌려주면 "어댑터가 없어 못 물어본
    # 날" 이 이력에 안 남는다 - 안 부른 것과 못 부른 것은 다르다.
    persistence.record_status(
        request_id=request_id,
        as_of=as_of,
        policy_version=policy_version,
        intent=intent.model_dump(mode="json"),
        outcome=outcome,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        # 읽기 축과 기록 축을 나눈다. 봉투 축은 읽을 장부이고, 이 행은 걷기가 만든 행이
        # 아니다. 정본 실행 축으로 적으면 그 실행의 이력(`count_runs_by_day` · 실행 목록의
        # 최근 활동)에 조회가 섞인다. 칸이 NULL 을 받으므로
        # (`master_agent_runs_sim_run_id.sql`) «걷기 밖» 으로 적는다.
        sim_run_id=None,
    )
    return outcome


def _record_selection(request: AskExecuteRequest) -> AskResponse:
    """사용자가 말로 고른 안을 결정 이력에 적는다 (역할 ⑦ 앞의 사람 게이트).

    여기서 새로 검사하지 않는다. 라벨이 그 실행에 실제로 있었나 · 지금 승인할 수 있는
    상태인가는 전부 `service/decision.py` 의 `record_decision` 이 한다. 발화문 경로라고
    검사를 따로 두면 두 경로의 승인 기준이 조용히 갈라진다 — 화면에서 누른 승인과 말로 한
    승인이 다른 규칙을 타면 안 된다.

    마스터 Flow 는 이 경로를 부를 수 없다. `flow.py` 는 `decision` 계열을 임포트하지
    않는다(8/26 회의 — 승인 게이트는 툴 목록 바깥).

    말에 없는 둘은 화면이 싣는다. 어느 실행인지(`target_request_id`)와 누가 승인하는지
    (`decided_by`)는 발화문에 없다. 없으면 추측하지 않고 거절한다.
    """
    intent = request.intent
    if not request.target_request_id:
        raise DecisionRejected(
            "어느 실행의 안인지 지정되지 않았다 — target_request_id 가 필요하다. "
            "발화문에는 그 정보가 없으므로 화면이 실어야 한다."
        )
    if not request.decided_by:
        raise DecisionRejected(
            "승인자가 없다 — decided_by 가 필요하다. 승인자가 없는 승인은 승인이 아니다."
        )

    decision = record_decision(
        request.target_request_id,
        DecisionIn(
            decision="APPROVE",
            scenario_label=intent.scenario_label,
            decided_by=request.decided_by,
            # 화면이 본 실행을 그대로 넘긴다 — 여기서 고르지 않는다.
            history_run_id=request.target_history_run_id,
            note="발화문 경로에서 선택",
        ),
    )
    return AskResponse(
        request_id=decision.request_id,
        as_of=request.as_of,
        outcome="DECISION_RECORDED",
        intent=intent,
        decision=decision,
        answer=_rule_answer(facts_from_decision(decision)),
        # ①도 ⑥도 안 부른다 — 이미 분류된 의도이고, 머리말이 이미 완결 문장이다.
        llm_status="SKIPPED_TEMPLATE",
    )


def _record_rerun(request: AskExecuteRequest) -> AskResponse:
    """조건을 붙인 재요청 — 적고 · 다시 돌리고 · 둘을 잇는다.

    ```text
    REQUEST_CHANGE 적재 → 새 업무 키로 재실행 → follow_up_request_id 로 연결
    ```

    조건을 숫자로 해석하지 않는다. "예산 2천만원으로 낮춰서" 를 재무 cap 으로 꽂으면
    마스터가 부서 판단을 덮어쓰는 것이다. 사용자의 말을 그대로 `prior_feedback` 으로
    매입에 넘기고, 해석은 매입이 한다(§3.2.2).

    제약: 지금 매입은 그 조건으로 안을 바꾸지 않는다. `prior_feedback` 을 `is_refeed`
    메타로만 읽는다(`purchase_agent/service/nodes/self_check.py`). 그래서 재실행 결과에 그
    사실을 적어 내보낸다 — 안 적으면 사용자는 조건이 반영된 줄 안다. 값을 실어 주고 안
    쓰는 것을 매입에 지적해 놓고 같은 일을 조용히 할 수는 없다.

    품목은 원 실행에서 가져온다. "예산 줄여서 다시 해줘" 에는 품목이 없다. 발화문에 없는
    것을 지어내지 않고 직전 실행이 무엇이었는지를 본다.
    """
    intent = request.intent
    if not request.target_request_id:
        raise DecisionRejected(
            "어느 실행에 대한 재요청인지 지정되지 않았다 — target_request_id 가 필요하다."
        )
    if not request.decided_by:
        raise DecisionRejected("요청자가 없다 — decided_by 가 필요하다.")
    if not intent.condition:
        raise DecisionRejected("조건이 비어 있다 — 조건 없는 재요청은 그냥 거절이다.")

    decision = record_decision(
        request.target_request_id,
        DecisionIn(
            decision="REQUEST_CHANGE",
            condition_text=intent.condition,
            decided_by=request.decided_by,
            history_run_id=request.target_history_run_id,
            note="발화문 경로에서 조건부 재요청",
        ),
    )

    follow_up_id = make_request_id(request.as_of.isoformat(), seq=decision.decision_seq + 1)
    rerun = run_procurement(
        ProcurementRunRequest(
            as_of=request.as_of,
            policy_version=request.policy_version,
            request_id=follow_up_id,
            item=intent.item or _item_of(request.target_request_id),
            budget=request.budget,
            sim_run_id=SHOWN_SIM_RUN_ID,
            prior_feedback={
                "condition_text": intent.condition,
                # 키 이름은 `attempt` 가 아니다 (#178). 두 슬롯(계약 v0.2 §2)은 수명·모양·
                # 권위가 다르므로 안의 키 이름도 다르게 둔다.
                #
                #     prior_feedback["condition_seq"]   사람이 조건을 건 회차   ← 여기
                #     feedback_context["attempt"]       매입 재호출 회차
                #
                # 같은 이름을 양쪽에 두면 매입이 `attempt` 로 되먹임 회차를 찾을 때 다른
                # 개념을 읽게 된다. `attempt` 는 되먹임 쪽이 가진다 — 매입
                # `constraints.yaml` 의 `attempt_max`(= `MAX_PURCHASE_ATTEMPTS` 인용)가 세는
                # 것이 그쪽이라 이름이 이미 그 뜻으로 쓰이고 있다.
                "condition_seq": decision.decision_seq,
                "requested_by": request.decided_by,
                "origin_request_id": request.target_request_id,
            },
        )
    )
    linked = link_follow_up(decision_id=decision.decision_id, follow_up_request_id=follow_up_id)

    # 답의 본체는 결정이 아니라 다시 만든 안이다. 사용자가 "다시 해줘" 라고 했으니 보고
    # 싶은 것은 새 안이다 — 결정 기록은 그 위에 한 줄로 붙인다.
    passed_on = (
        f"조건 '{intent.condition}' 을 매입에 그대로 전달했습니다 — "
        "다만 매입은 아직 이 조건으로 안을 바꾸지 않습니다(재요청 표시로만 씁니다)"
    )
    unlinked = () if linked else ("이 결정에는 이미 후속 실행이 있어 링크를 잇지 않았습니다",)
    base = facts_from_procurement(rerun)
    facts = replace(
        base,
        facts=(
            *base.facts,
            Fact(label="조건 기록", value=f"{decision.decision_seq}회차 · {intent.condition}"),
            Fact(label="원 실행", value=request.target_request_id),
        ),
        gaps=(*base.gaps, passed_on, *unlinked),
    )
    rerun.report_text = render_answer(facts)
    return AskResponse(
        request_id=follow_up_id,
        as_of=request.as_of,
        outcome="DECISION_RECORDED",
        intent=intent,
        decision=decision.model_copy(update={"follow_up_request_id": follow_up_id}),
        run=rerun,
        answer=_rule_answer(facts),
        llm_status="SKIPPED_TEMPLATE",
    )


def _shown_note(as_of: date) -> str:
    """조회가 어느 실행·기준일을 읽었나. 재무 현금 그래프 문장과 같은 모양이다."""
    return f"보고 있는 실행: {SHOWN_SIM_RUN_ID} · 기준일: {as_of.isoformat()}"


def _item_of(request_id: str) -> str | None:
    """직전 실행의 품목. 없으면 비운다 — 지어내지 않는다."""
    try:
        history = get_run_history(request_id)
    except LookupError:
        return None
    item = (history.request_payload or {}).get("item")
    return item if isinstance(item, str) else None


def _rule_answer(facts: AnswerFacts) -> AnswerOut:
    """⑥ 없이 규칙만으로 만드는 답.

    머리말이 이미 완결된 판단 문장인 곳에는 ⑥ 을 얹지 않는다. "'기본' 안으로
    진행합니다" · "매입안을 제시합니다" 위에 한 문장을 더 쓰면 같은 말을 두 번 한다
    (실측에서 그랬다). 조회만 머리말이 머리글("조회 결과 — 물류")이라 얹을 자리가 있다.
    """
    return AnswerOut(text=render_answer(facts), llm_status="SKIPPED_TEMPLATE")


def _write_answer(facts: AnswerFacts, narrator: NarrativeService | None) -> AnswerOut:
    """⑥ — 문장을 얹어 사람이 읽는 답을 만든다.

    문장 생성이 실패해도 답은 나간다. `narrative=None` 이면 규칙이 만든 사실 줄만으로
    완결된다 — LLM 을 답의 뼈대로 쓰지 않는 것이 이 설계의 요지다.
    """
    if facts.markdown:
        # 부서가 완결한 본문이 있으면 ⑥을 부르지 않는다. 가격 예측의 마크다운은 이미
        # 사람에게 쓴 답이라, 문장을 얹으면 같은 말을 두 번 하거나 요약이 본문과
        # 어긋난다 — 매입 머리말에 ⑥을 안 붙이는 것과 같은 이유다.
        return AnswerOut(
            text=render_answer(facts),
            markdown=facts.markdown,
            llm_status="SKIPPED_TEMPLATE",
        )
    narrator = narrator or get_narrative_service()
    result = narrator.write(facts)
    return AnswerOut(
        text=render_answer(facts, result.narrative),
        narrative=result.narrative,
        llm_status=result.llm_status,
        llm_attempts=result.llm_attempts,
        llm_fallback_used=result.llm_fallback_used,
    )


def _to_answer(outcome: StatusOutcome) -> StatusAnswer:
    return StatusAnswer(
        status_code=outcome.status_code,
        reason=outcome.reason,
        answers={k: dict(v) for k, v in outcome.answers.items()},
        unavailable=list(outcome.unavailable),
        missing_data={k: list(v) for k, v in outcome.missing_data.items()},
        errors=dict(outcome.errors),
    )


def _response(
    request_id: str,
    request: AskRequest,
    result: IntentResult,
    *,
    outcome,
    confirm_required: bool = False,
    status: StatusAnswer | None = None,
    answer: AnswerOut | None = None,
    note: str | None = None,
    clarification: str | None = None,
    domain_result: DomainActionAnswer | None = None,
) -> AskResponse:
    return AskResponse(
        request_id=request_id,
        as_of=request.as_of,
        outcome=outcome,
        intent=result.intent,
        clarification=clarification if clarification is not None else result.clarification,
        confirm_required=confirm_required,
        status=status,
        answer=answer,
        domain_result=domain_result,
        llm_status=result.llm_status,
        llm_provider=result.llm_provider,
        llm_model=result.llm_model,
        llm_attempts=result.llm_attempts,
        llm_fallback_used=result.llm_fallback_used,
        note=note,
    )
