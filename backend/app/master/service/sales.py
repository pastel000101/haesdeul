"""판매 의사결정 실행 — 관문 · 경계 · 예측을 모아 판매 Flow 를 돌리고 이력을 남긴다.

매입 쪽 진입점은 `service/procurement.py` 다.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.contracts.envelope import ExecutionContext
from app.master.domain.execution_day import CalendarNotCovered
from app.master.domain.request_ids import make_request_id
from app.master.domain.run_response import empty_sales_response, to_sales_response
from app.master.domain.sim_run import sim_run_id_of
from app.master.readmodel.holiday_calendar import get_calendar
from app.master.readmodel.inputs import load_forecast
from app.master.readmodel.procurement_boundary import read_procurement_boundary
from app.master.registry import wiring
from app.master.schemas.inputs import SALES_TARGET_KIND, SourcedInput
from app.master.schemas.procurement_boundary import ProcurementBoundary
from app.master.schemas.sales import SalesRunRequest, SalesRunResponse
from app.master.service import persistence
from app.master.service.day_gate import check_day_gate
from app.master.service.procurement import elapsed
from app.master.service.runner import MasterRunner
from app.master.service.sales_flow import SalesFlow, sales_call_budget
from app.master.service.verifier import SalesVerificationContext, SalesVerifier, SalesVerifierPort

logger = logging.getLogger(__name__)

def run_sales(
    request: SalesRunRequest,
    verifier: SalesVerifierPort | None = None,
) -> SalesRunResponse:
    """판매 Flow 를 한 번 돌리고 이력에 남긴다 (설계 2026-09-07 §0).

    ```text
    개장 Gate  →  필수 어댑터 점검  →  SalesFlow  →  검증(Critic B)  →  이력 적재
    ```

    판단은 검증(Critic B)을 지나간다 — 매입처럼 판단 안에서 돈다. `verifier` 를 주지
    않으면 기본 검증 Tool 이 붙는다(`SalesVerifier`) — 매입 `run_procurement` 와 같은
    규율이다. 끄려면 명시적으로 꺼야 한다.

    주의: 배분·로트 재료가 없으면 온전한 판정이 나지 않고 `skipped_checks` 에 "못
    냈다" 가 남는다 — 통과가 아니다(§3.7.6).

    실행일 Gate 를 걸지 않는다 — 주말에도 판다. 두 관문은 다른 물음이다. 개장은 "그 날
    장부가 열렸는가" 라 판매·매입이 같이 지나고, 실행일은 "장이 서서 ML 예측이 있는가"
    라 매입만 지난다. 팔 때는 예측이 필요 없다 — 그것이 개장을 달력일로 정한 이유다
    (설계 §1). 토요일 요청은 개장을 통과하고 매입은 실행일에서 서지만 판매는 그대로
    간다. 여기에 `_execution_day_verdict` 를 복사해 넣으면 2026년 토요일 45일에 판매가
    멈춘다.

    필수 어댑터는 부르기 전에 본다(`wiring.REQUIRED_FOR_SALES`). 골격이
    `AgentNotRegistered` 를 `SL4_NOT_STARTED` 로 받으므로 실패하지는 않는다
    (`sales_flow.py` 의 `run`). 하지만 그때는 이미 다른 부서를 부른 뒤일 수 있다 — 한
    번이라도 부르면 그 회신이 이력에 남고, 나중에 읽는 사람이 "돌긴 돌았다" 로 읽는다.
    매입이 `wiring.missing()` 으로 문 앞에서 접는 것과 같은 이유다.

    필수는 제안자와 최종 검증자 둘뿐이다. 물류는 여기 없다 — 판매는 밴드가 없어 물류가
    못 답해도 시작한다(설계 §1-2). 목록이 왜 그 둘인지는 `registry/wiring.py` 에 적혀
    있고, 여기서 다시 정하지 않는다.

    매입과 응답 조립을 공유하지 않는다. 응답 모델도 종료 코드도 다르다 — 묶으면 판매
    종료 코드가 매입 어휘로 새거나 그 반대가 된다(설계 §1).
    """
    verifier = SalesVerifier() if verifier is None else verifier
    started = time.perf_counter()
    request_id = request.request_id or make_request_id(request.as_of.isoformat())
    context = ExecutionContext(
        request_id=request_id,
        as_of=request.as_of,
        trigger=request.trigger,
        policy_version=request.policy_version,
        # 어느 실행의 장부인가는 마스터가 정한다(물류 `#325`) — 매입과 같은 값이다.
        # 판매도 물류를 부르므로(`PRE_SALES`) 같은 이유가 그대로 걸린다.
        #
        # 요청이 주면 그 값이 이긴다 — 매입과 같다(`#531`). 여기만 상수로 남기면
        # 같은 날 매입 행과 판매 행이 서로 다른 실행에 앉고, `_procurement_boundary` 가
        # 남의 실행의 경계를 읽는다.
        sim_run_id=sim_run_id_of(request),
    )

    # 첫 관문은 개장이다 — 매입과 같은 판정 함수를 부른다.
    # 잊으면 "안 열린 날 판매가 돈다" 인데, 막힌 게 아니라 안 막힌 것이라
    # 아무 오류도 나지 않는다. 그 조용한 실수를
    # `tests/master/test_entrypoint_day_gate.py` 가 먼저 잡는다.
    day_gate = check_day_gate(request.as_of, sim_run_id=context.sim_run_id)
    if day_gate.gate == "BLOCKED":
        response = empty_sales_response(
            context,
            reason=day_gate.reason or "그날 장부가 안 열렸다",
            skipped_note="전 검사: 그날이 안 열려서 판매 Flow 가 시작되지 않음",
        )
        response.day_gate = day_gate
        response.report_text = _sales_fold_note(response.end_code, response.reason)
        response.history_run_id = persistence.record_sales(
            request, response, elapsed_ms=elapsed(started), sim_run_id=context.sim_run_id
        )
        return response

    # 두 번째 관문은 배선이다 — 개장 다음이다. 안 열린 날인데 "어댑터 미등록" 이라고
    # 답하면 사람이 배선을 뒤지는데, 실제로는 그 날을 다시 열 일이다.
    #
    # 빠진 이름을 사유에 적는다. "어댑터 미등록" 만 적으면 무엇을 배선해야
    # 하는지 모른 채 코드를 뒤지게 된다.
    missing = wiring.missing(wiring.REQUIRED_FOR_SALES)
    if missing:
        # 미등록은 오류가 아니라 상태다 (§5.3) — 매입 갈래와 같은 태도, 판매 어휘.
        response = empty_sales_response(
            context,
            reason=f"어댑터 미등록: {', '.join(missing)}",
            skipped_note="전 검사: 어댑터가 없어 판매 Flow 가 시작되지 않음",
        )
        response.day_gate = day_gate
        response.report_text = _sales_fold_note(response.end_code, response.reason)
        # 못 부른 날도 이력에 남긴다. 안 부른 것과 못 부른 것은 다르고,
        # 이력이 비면 둘이 같아 보인다.
        response.history_run_id = persistence.record_sales(
            request, response, elapsed_ms=elapsed(started), sim_run_id=context.sim_run_id
        )
        return response

    # ML 예측은 마스터가 읽어 실어 준다(판매 v1.7 §11 · M-1). 판매는 ML 을
    # 직접 부르지 않는다 — 부를 대상이 없다(`readmodel/inputs.py` 머리말).
    #
    # 매입과 같은 자리다. 매입은 `_inputs_for(request)` 로 읽어
    # `ProcurementFlow(forecast=...)` 로 넘긴다. 여기도 진입점이 읽고 Flow 에 넘긴다 —
    # Flow 가 직접 조회하면 조립기가 적재층을 겸하게 되고, 백테스트가 그날 값을
    # 꽂아 넣을 자리도 사라진다.
    forecast = _sales_forecast(request)
    runner = MasterRunner(context, wiring.registry(), sales_call_budget(request.budget))
    outcome = SalesFlow(
        runner,
        # 매입에 실어 줄 경계 재료를 여기서 읽는다(`ADDITIONAL_SUPPLY_CONTEXT`).
        # `forecast` 와 같은 자리다 — 진입점이 읽고 Flow 에 넘긴다. Flow 가 직접
        # 조회하면 조립기가 적재층을 겸하게 된다.
        #
        # 부서 호출이 0회다. 값은 그날 매입 판단이 `PRE_PURCHASE` 로 받아 실행
        # 이력에 적어 둔 것이라, 물류·재무를 다시 부르지 않는다.
        procurement_boundary=_procurement_boundary(context),
        user_request=_sales_user_request(request),
        # 최상위 필수 칸이라 따로 나른다(`SalesProposalInput.business_mode`).
        # `_sales_user_request` 안에 넣으면 판매 `SalesUserRequest` 가
        # `extra="forbid"` 라 요청 전체가 문 앞에서 거부된다.
        business_mode=request.business_mode,
        forecast=forecast.payload if forecast.usable else None,
        # 못 읽은 이유를 아는 곳은 여기다. Flow 는 자기가 아는 이유(look-ahead)만 쓴다.
        forecast_note=_sales_forecast_note(forecast),
        # mock 이면 세운다 — 매입과 같은 태도다(`SalesFlow.mocked_inputs`).
        # 실어 주는 쪽과 막는 쪽을 같이 잇는다.
        mocked_inputs=("forecast",) if forecast.grade == "MOCK" else (),
    ).run()

    response = to_sales_response(context, outcome)
    response.day_gate = day_gate

    # 판단이 Critic B 를 지나간다. 응답을 만든 뒤에 붙이는 이유는 검증이 후보와
    # 물류 컨텍스트를 둘 다 봐야 해서다 — 둘은 Flow 결과에만 같이 있다.
    #
    # 매입이 싣는 모양을 그대로 따른다 — findings · concerns · skipped_checks.
    verification = verifier(
        SalesVerificationContext(
            as_of=request.as_of,
            item=request.item,
            candidates=tuple(c.scenario for c in outcome.candidates),
            supply_context=outcome.supply_context,
        )
    )
    response.findings = list(verification.findings)
    response.concerns = list(verification.concerns)
    response.skipped_checks = list(verification.skipped)

    response.report_text = _sales_fold_note(response.end_code, response.reason)
    response.history_run_id = persistence.record_sales(
        request, response, elapsed_ms=elapsed(started), sim_run_id=context.sim_run_id
    )
    return response


def _procurement_boundary(context: ExecutionContext) -> ProcurementBoundary | None:
    """그날 매입 판단이 받아 둔 경계. 못 읽어도 사이클을 세우지 않는다.

    못 읽은 것과 안 읽은 것은 다르다.

    ```text
    ProcurementBoundary(present=False, absent_reason=…)   물어봤는데 없다  → 사유가 봉투에 실린다
    None                                                  읽어 보지도 못했다
    ```

    달력이 그날을 덮지 않으면 주말 축만으로 다시 읽는다(`procurement.py` 의
    `_execution_day_verdict` 와 같은 처리). 잡지 않으면 공휴일 달력이 끊긴 날 판매
    사이클 전체가 선다 — 경계는 조건부 재료이지 판매를 돌릴 수 있는지의 조건이 아니다.

    `sim_run_id` 가 비면 읽지 않는다. `read_procurement_boundary` 가 빈 축을
    `ValueError` 로 막는데("빈 축을 조용히 전체로 바꾸지 않는다"), 그것을 여기서
    터뜨리면 봉투 `sim_run_id` 를 채우지 않은 경로가 판매를 못 돌린다. 그 값은
    `ExecutionContext` 에서 아직 필수가 아닌 칸이다(그 docstring 의 ①②③).
    """
    if not context.sim_run_id.strip():
        return None
    try:
        return read_procurement_boundary(
            as_of=context.as_of, sim_run_id=context.sim_run_id, calendar=get_calendar()
        )
    except CalendarNotCovered:
        return read_procurement_boundary(as_of=context.as_of, sim_run_id=context.sim_run_id)


def _sales_forecast(request: SalesRunRequest) -> SourcedInput:
    """판매에 실어 줄 ML 예측 하나(§3.2.5 예외 · M-1).

    셋을 다 모으지 않는다. 매입은 `collect_inputs` 로 예측·확정주문·정책값을 한꺼번에
    읽지만 판매가 나르는 것은 예측뿐이다. 셋을 읽으면 판매가 쓰지도 않는
    `policy_values` 가 mock 인 날 판매가 서고, 그것은 없는 이유로 멈추는 것이다.

    실패 처리: 못 읽어도 예외를 올리지 않는다 — 매입 `_inputs_for` 와 같은 태도다. ML
    DB 가 죽었다고 판매가 통째로 못 도는 것은 아니다(판매 v1.7: "ML missing 은 전체
    Sales 실패가 아니다"). 대신 못 읽었다는 사실이 `SourcedInput` 에 남아 Flow 를 거쳐
    응답까지 간다.

    품목이 없으면 묻지 않는다 — 예측은 품목별이라 물을 대상이 없다.
    """
    if not request.item:
        return SourcedInput(
            key="forecast",
            payload=None,
            grade="MISSING",
            source="-",
            note="품목이 없어 ML 예측을 읽지 않았다",
        )
    try:
        # 중도매 계열이다. 경매가 아니다. 매입과 같은 경매가를 읽으면 경매가로 사서
        # 경매가로 파는 셈이다 — 실측 `SIM-CHAIN-V3` 1~3월에서
        # `SALES_MARGIN_BELOW_MINIMUM` 이 511건 중 483건. 왜 중도매인지는
        # `schemas/inputs.py` 의 `SALES_TARGET_KIND` 한 자리에 적혀 있다.
        return load_forecast(request.item, request.as_of, target_kind=SALES_TARGET_KIND)
    except Exception as exc:
        # 조회 실패가 판매 실행을 막지 않는다. 다만 조용히 넘어가지도 않는다 —
        # `procurement.py` 의 `_approved_commitments` 와 같은 태도다.
        logger.exception("판매에 실을 ML 예측 조회 실패 - 실행은 그대로 돈다")
        return SourcedInput(
            key="forecast",
            payload=None,
            grade="MISSING",
            source="-",
            note=f"ML 예측 조회 실패 ({type(exc).__name__})",
        )


def _sales_forecast_note(forecast: SourcedInput) -> str:
    """못 실은 이유 한 줄. 실을 수 있으면 빈 문자열이다.

    문장을 새로 짓지 않는다. 적재층이 쓴 `note` 를 그대로 옮기고, 없을 때만 등급을
    적는다(`AgentFailure.detail` 과 같은 자리).
    """
    if forecast.usable:
        return ""
    return forecast.note or f"ML 예측을 못 실었다 ({forecast.grade})"


def _sales_user_request(request: SalesRunRequest) -> dict[str, Any] | None:
    """판매에 실어 보낼 사용자 요청. 묶기만 하고 해석하지 않는다(§3.2.2).

    칸 이름은 판매 것이다(`app/sales/schemas/proposal.py` `SalesUserRequest`) —
    `raw_text` · `item` · `partner_id`. 받는 쪽 낱말에 맞춘다.

    없는 칸은 만들지 않는다. 빈 값을 실으면 받는 쪽이 "사용자가 말 안 했다" 와
    "마스터가 안 보낸다" 를 구별할 수 없다(§1.2-10).

    `business_mode` 는 여기 들어가지 않는다. 판매 쪽 `SalesUserRequest` 는
    `extra="forbid"` 이고 `business_mode` 는 그 바깥(`SalesProposalInput` 최상위)에
    있다. 그 최상위 칸은 `SalesFlow(business_mode=...)` 가 나른다 — 여기 넣으면 판매 문
    앞에서 통째로 거부된다.

    수량은 구조화된 칸으로 나른다. 판매는 `raw_text` 를 해석해 수량을 뽑지 않아서,
    자유 문장만 보내면 `PROPOSAL_QUANTITY_REQUIRED` 로 되돌아온다. 그래서
    `SalesRunRequest.requested_quantity_kg` 를 여기서 이름만 바꿔 옮긴다 — 값을
    만들지도 반올림하지도 않는다.

    상업조건 셋도 나른다: `preferred_unit_price_krw` · `preferred_payment_terms_type` ·
    `source_ref`. 없으면 재무가 `SALES_INPUT_INCOMPLETE` 로 판정을 내지 못한다. 값은
    부르는 쪽이 준다 — 자동 걷기는 실행 규칙에서(`sales_terms.py`), 화면은 사람에게서.

    나르지 않는 칸이 있다(`preferred_contract_term_days`). 요구한 caller 가 없어서인데,
    그 사실은 `tests/master/test_sales_user_request_fields.py` 가 판매 모델과 대조해
    지킨다. `allow_additional_sourcing` 은 사용자가 동의한 `True` 일 때만 나른다.
    """
    payload: dict[str, Any] = {}
    if request.user_request:
        payload["raw_text"] = request.user_request
    if request.item:
        payload["item"] = request.item
    if request.partner_id:
        payload["partner_id"] = request.partner_id
    if request.requested_quantity_kg is not None:
        # 0 도 사실이다. `if request.requested_quantity_kg` 로 적으면 "0kg 을
        # 말했다" 가 "말하지 않았다" 로 접힌다.
        #
        # `Decimal` 을 전선에 그대로 싣지 않는다(#175 와 같은 규율).
        # `json.dumps` 가 `Decimal` 에서 죽는다 — 봉투 payload 는 이력으로 한 번
        # JSON 을 왕복하므로 여기서 편다. 봉투 표준형이 `target_value: float` 인
        # 것과 같은 자리이고, 판매 쪽 `Decimal` 파싱은 `str(float)` 을 거쳐
        # 값이 그대로다.
        payload["requested_quantity_kg"] = float(request.requested_quantity_kg)
    if request.preferred_delivery_date is not None:
        # 없으면 싣지 않는다. 오늘이나 `as_of + N` 으로 대신 채우지 않는다 —
        # 그 값은 승인되면 `sales.sale_date` 가 되고 수금 기일의 출발점이 된다.
        payload["preferred_delivery_date"] = request.preferred_delivery_date.isoformat()
    if request.preferred_payment_days is not None:
        # 0 도 사실이다(`requested_quantity_kg` 과 같은 자리). `payment_days=0`
        # 은 "당일 수금" 이라는 정해진 조건이지 "말하지 않았다" 가 아니다.
        payload["preferred_payment_days"] = request.preferred_payment_days
    if request.preferred_unit_price_krw is not None:
        # `Decimal` 을 전선에 그대로 싣지 않는다(`requested_quantity_kg` 과
        # 같은 자리 · `json.dumps` 가 `Decimal` 에서 죽는다).
        payload["preferred_unit_price_krw"] = float(request.preferred_unit_price_krw)
    if request.preferred_payment_terms_type:
        payload["preferred_payment_terms_type"] = request.preferred_payment_terms_type
    if request.source_ref:
        # 이 칸은 마스터가 짓지 않는다. 되짚을 행이 있을 때만 부르는 쪽이
        # 채워 보내고, 여기서는 이름만 맞춰 옮긴다.
        payload["source_ref"] = request.source_ref
    # 기본 false는 Sales 모델의 기본값에 맡기고, 사용자가 동의한 true만 전달한다.
    if request.allow_additional_sourcing:
        payload["allow_additional_sourcing"] = True
    return payload or None


def _sales_fold_note(end_code: str, reason: str) -> str:
    """접힌 날 한 줄. `SL1` 에서는 빈 문자열이다(설계 §5).

    마스터가 판매 문장을 짓지 않는다. 추천 문장과 순위는 판매 소유이고 마스터는 순위를
    재계산하지 않는다(판매 v1.7 §18). 매입은 `render_answer` 로 리포트를 짓지만 판매는
    판매 것을 나른다 — 여기서 문장을 만들기 시작하면 마스터가 두 번째 추천자가 된다.

    다만 Flow 가 접힌 날(`SL2`~`SL6`)은 판매 문장 자체가 없다. 그때만 왜 접혔는지를
    적고, 그것도 실행 사실이지 업무 판단이 아니다 — "이 조건으로는 못 판다" 가 아니라
    "후보를 못 받았다" 라고 쓴다.
    """
    말 = {
        "SL2_NO_CANDIDATE": "판매가 후보를 내지 못해 접혔다",
        "SL3_ALL_REJECTED": "후보가 전부 탈락해 접혔다",
        # 탈락이라고 쓰지 않는다. 후보는 살아 있고 판정만 안 끝났다 —
        # 여기서 "탈락" 이라고 적으면 종료 코드를 나눈 뜻이 문장에서 사라진다.
        "SL6_VALIDATION_UNRESOLVED": "후보는 있으나 검증이 끝나지 않아 접혔다",
        "SL4_NOT_STARTED": "시작하지 못했다",
        "SL5_BUDGET_EXHAUSTED": "호출 예산이 다해 판단이 끝나지 않았다",
    }.get(end_code)
    if 말 is None:
        # `SL1_PRESENTED` — 문장은 판매가 낸 것이 답이다.
        return ""
    return f"{말} ({end_code}): {reason}"
