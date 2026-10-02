"""매입 · 판매 실행 결과 → 응답 모델 변환 — Flow 결과를 `schemas/` 응답으로 옮긴다."""

from __future__ import annotations

from collections.abc import Mapping

from app.contracts.envelope import ExecutionContext
from app.master.domain.flow import ProcurementOutcome
from app.master.domain.plan import ExecutionPlan
from app.master.domain.sales_flow import SalesOutcome
from app.master.schemas.inputs import MasterInputs
from app.master.schemas.procurement import ProcurementRunResponse
from app.master.schemas.run_response import AdjustmentOut, BlockedAgentOut, EvidenceOut, StepOut
from app.master.schemas.sales import SalesCandidateOut, SalesRunResponse

# ---------------------------------------------------------------------------
# 변환
# ---------------------------------------------------------------------------


def to_response(
    context: ExecutionContext,
    outcome: ProcurementOutcome,
    inputs: MasterInputs | None = None,
    sources: Mapping[str, str] | None = None,
) -> ProcurementRunResponse:
    """출처표는 부서 payload 와 같은 표다 (`service/procurement.py` 의 `_input_sources`).

    같은 실행에서 `run_procurement` 이 한 번 만든 것을 그대로 받는다 — 여기서 다시
    `inputs.sources()` 를 부르면 주입분이 빠져 화면과 payload 가 갈린다.

    `mocked_inputs` 는 여기서도 `inputs.mocked` 그대로다. 주입은 mock 이 아니라
    섞지 않는다 (`inputs.injected_keys`).
    """
    if sources is None:
        sources = inputs.sources() if inputs else {}
    return ProcurementRunResponse(
        input_sources=dict(sources),
        mocked_inputs=list(inputs.mocked) if inputs else [],
        request_id=context.request_id,
        as_of=context.as_of,
        end_code=outcome.end_code,
        reason=outcome.reason,
        scenarios=[dict(s) for s in outcome.scenarios],
        judgment=dict(outcome.judgment),
        constraints={k: dict(v) for k, v in outcome.constraints.items()},
        evidences=evidences_out(outcome),
        adjustments=adjustments_out(outcome),
        verdicts={k: dict(v) for k, v in outcome.verdicts.items()},
        blocked_by=list(outcome.blocked_by),
        blocked_failures=blocked_out(outcome),
        findings=list(outcome.findings),
        concerns=list(outcome.concerns),
        skipped_checks=list(outcome.skipped_checks),
        verification_skipped=outcome.verification_skipped,
        purchase_attempts=outcome.purchase_attempts,
        presentable=outcome.presentable,
        single_option=outcome.single_option,
        plan=steps(outcome.plan),
        plan_signature=list(outcome.plan.signature),
    )


def empty_response(
    context: ExecutionContext,
    reason: str,
    missing_adapters: list[str] | None = None,
    skipped_note: str = "전 검사: 어댑터 미등록으로 Flow 가 시작되지 않음",
) -> ProcurementRunResponse:
    """안 돈 날의 응답. 왜 안 돌았는지가 `reason` 과 `skipped_note` 로 남는다.

    `skipped_note` 는 기본값이 어댑터 문구다. 다른 이유로 접을 때 그대로 쓰면
    없던 어댑터 문제를 지어내는 것이 되므로 부르는 쪽이 자기 사유를 준다.
    """
    adapters = list(missing_adapters or [])
    return ProcurementRunResponse(
        request_id=context.request_id,
        as_of=context.as_of,
        end_code="E4_NOT_STARTED",
        reason=reason,
        blocked_by=adapters,
        missing_adapters=adapters,
        verification_skipped=True,
        # 못 돈 날도 무엇을 못 봤는지는 남긴다 (§3.7.6)
        skipped_checks=[skipped_note],
    )


def to_sales_response(context: ExecutionContext, outcome: SalesOutcome) -> SalesRunResponse:
    """판매 Flow 결과를 응답 모양으로. 매입 `to_response` 와 따로 둔다.

    공통 조립 함수로 묶지 않는 자리다 (설계 §1). 두 사이클은 응답 모델도 종료
    코드도 다르고, 공유하는 것은 판정(`check_day_gate`)과 순서이지 응답이 아니다.
    """
    return SalesRunResponse(
        request_id=context.request_id,
        as_of=context.as_of,
        end_code=outcome.end_code,
        reason=outcome.reason,
        candidates=sales_candidates_out(outcome),
        judgment=dict(outcome.judgment),
        supply_context=dict(outcome.supply_context),
        context_failure=sales_context_failure_out(outcome),
        # 못 실은 사실을 응답까지 나른다 (M-1). 매입 `mocked_inputs` ·
        # `input_sources` 와 같은 자리다 — 값이 어디서 왔는지(또는 안 왔는지)를
        # 결론과 떼어 놓지 않는다.
        ml_context_note=outcome.ml_context_note,
        # 근거·조정안은 고르지도 정렬하지도 않는다 — 매입과 같은 함수를 쓴다.
        # 부서가 낸 차례가 그 부서의 설명 순서다 (§3.2.2).
        evidences=evidences_out(outcome),
        adjustments=adjustments_out(outcome),
        feedback_attempts=outcome.feedback_attempts,
        plan=steps(outcome.plan),
        plan_signature=list(outcome.plan.signature),
    )


def sales_candidates_out(outcome: SalesOutcome) -> list[SalesCandidateOut]:
    """후보와 그 판정을 옮긴다. 통과 판정을 여기서 다시 세지 않는다.

    `passed` · `unvalidated` · `detail` 은 `CandidateVerdict` 의 property 를 그대로
    읽는다. 허용목록(`PASSING_VERDICTS`)이 늘어도 답이 한 곳에서만 바뀐다 — 여기서
    "reject 가 아니면 통과" 로 다시 세면 어휘가 는 날 새 값이 통과 쪽으로 샌다.
    """
    return [
        SalesCandidateOut(
            scenario=dict(c.scenario),
            validations={k: dict(v) for k, v in c.validations.items()},
            unroutable=list(c.unroutable),
            missing_terms=list(c.missing_terms),
            passed=c.passed,
            unvalidated=c.unvalidated,
            detail=c.detail,
        )
        for c in outcome.candidates
    ]


def sales_context_failure_out(outcome: SalesOutcome) -> BlockedAgentOut | None:
    """물류가 초기 컨텍스트를 못 낸 사실. 없으면 `None` 이다.

    `detail` 을 여기서 다시 만들지 않고 `AgentFailure.detail` 을 부른다 — 매입
    `blocked_out` 과 같은 자리다.
    """
    failure = outcome.context_failure
    if failure is None:
        return None
    return BlockedAgentOut(
        agent=failure.agent,
        runtime_status=failure.runtime_status,
        reasoning=failure.reasoning,
        missing_data=list(failure.missing_data),
        detail=failure.detail,
    )


def empty_sales_response(
    context: ExecutionContext, reason: str, skipped_note: str = ""
) -> SalesRunResponse:
    """시작조차 못 한 날의 판매 응답.

    `SL4_NOT_STARTED` 다. 매입의 `E4` 와 뜻은 같지만 어휘는 갈려 있다 —
    한 어휘에 두 사이클을 담으면 화면과 이력이 어느 사이클의 종료인지를 payload 로
    되짚어야 한다 (D-3 합의).

    검증이 안 돈 날이다. `verification_skipped` 를 세우고 무엇을 못 봤는지도
    남긴다 — 매입 `empty_response` 와 같은 자리다. 비워 두면 빈 `findings` 가
    "Critic 을 지나 통과했다" 로 읽힌다 (§3.7.6).
    """
    return SalesRunResponse(
        request_id=context.request_id,
        as_of=context.as_of,
        end_code="SL4_NOT_STARTED",
        reason=reason,
        verification_skipped=True,
        skipped_checks=[skipped_note] if skipped_note else [],
    )


def evidences_out(outcome: ProcurementOutcome | SalesOutcome) -> list[EvidenceOut]:
    """부서 근거를 응답 모양으로. 고르지도 요약하지도 않는다.

    두 사이클이 같이 쓴다. 근거를 옮기는 규칙은 사이클에 매인 것이 아니라
    봉투 수준의 것이다 (`SourcedEvidence` 를 봉투로 올린 것과 같은 이유). 베끼면
    한쪽만 고쳐지는 날이 온다 — 응답 모델을 안 묶는 것과 다른 이야기다.

    순서를 손대지 않는다 - 부서가 낸 순서가 그 부서의 설명 순서다.
    마스터가 정렬하면 "이게 더 중요하다" 는 뜻이 생긴다 (§3.2.2).

    빈 목록은 "근거가 완비됐다" 가 아니라 부서가 근거를 안 냈다는 사실이다.
    화면이 그렇게 읽도록 스키마 설명에 적어 두었다.
    """
    return [
        EvidenceOut(
            agent=item.agent,
            mode=item.mode,
            claim=item.evidence.claim,
            source=item.evidence.source,
            value=item.evidence.value,
            unit=item.evidence.unit,
            evidence_grade=item.evidence.evidence_grade,
            evidence_detail=item.evidence.evidence_detail,
            ref_ids=list(item.evidence.ref_ids),
        )
        for item in outcome.evidences
    ]


def adjustments_out(outcome: ProcurementOutcome | SalesOutcome) -> list[AdjustmentOut]:
    """조정안을 표준형 그대로 옮긴다. 고르지도 정렬하지도 않는다.

    근거(`evidences_out`)와 같이 두 사이클이 같이 쓴다 — 표준형은 봉투 것이다.

    순서는 부서가 보낸 차례다. 정렬하면 그것이 우선순위로 읽힌다 -
    근거(`evidences_out`)와 같은 이유다 (§3.2.2).
    """
    return [
        AdjustmentOut(
            dept=a.dept,
            axis=a.axis,
            target_value=a.target_value,
            unit=a.unit,
            reason=a.reason,
            ref_ids=list(a.ref_ids),
            # 부서가 안 채우면 빈 목록·None 그대로 나간다 - 없는 것을 만들지 않는다
            scenario_labels=list(a.scenario_labels),
            split_date=a.split_date,
        )
        for a in outcome.adjustments
    ]


def blocked_out(outcome: ProcurementOutcome) -> list[BlockedAgentOut]:
    """막은 부서를 사유째로 옮긴다. 순서를 바꾸지 않는다.

    `detail` 을 여기서 다시 만들지 않고 `AgentFailure.detail` 을 부른다 -
    `reason` 문장에 들어간 것과 같은 함수여야 둘이 갈리지 않는다.
    """
    return [
        BlockedAgentOut(
            agent=f.agent,
            runtime_status=f.runtime_status,
            reasoning=f.reasoning,
            missing_data=list(f.missing_data),
            detail=f.detail,
        )
        for f in outcome.blocked_failures
    ]


def steps(plan: ExecutionPlan) -> list[StepOut]:
    return [
        StepOut(
            seq=s.seq,
            agent=s.agent,
            mode=s.mode,
            call_seq=s.call_seq,
            run_id=s.run_id,
            runtime_status=s.runtime_status,
            business_status=s.business_status,
            used_tools=list(s.used_tools),
            finding_codes=list(s.finding_codes),
            missing_data=list(s.missing_data),
            reasoning=s.reasoning,
            llm_status=s.llm_status,
            llm_model=s.llm_model,
            llm_attempts=s.llm_attempts,
            llm_fallback_used=s.llm_fallback_used,
            replans=s.replans,
            # `None` 을 메우지 않는다. 여기서 `plan.as_of` 를 넣으면
            # 안 잰 부서가 잰 부서처럼 화면에 선다.
            observed_at=s.observed_at,
            # 마스터는 읽지 않고 나른다 - 순서도 부서가 낸 그대로다
            observations=list(s.observations),
        )
        for s in plan.steps
    ]
