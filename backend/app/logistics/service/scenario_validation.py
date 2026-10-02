"""SCENARIO_VALIDATION — 매입 시나리오별 창고 판정과 (opt-in) LLM 해석."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.contracts.core import SuggestedAdjustment
from app.contracts.envelope import AgentReply, AgentRequest, ExecutionMetadata
from app.logistics.domain.agent_evidence import (
    VERDICT_MAP,
    as_proposal,
    evidence,
    inventory_by_item_evidences,
    policy_ref,
    snapshot_ref,
    to_float,
)
from app.logistics.domain.agent_replies import (
    AGENT,
    T_ARRIVAL,
    T_CAP,
    T_RULES,
    T_SIGNALS,
    execution_meta,
    logistics_run_id,
    not_ready_reply,
    snapshot_error_reply,
)
from app.logistics.domain.rules import (
    derive_procurement_verdict,
    evaluate_procurement_business_signals,
    evaluate_procurement_rules,
    merge_business_warnings,
)
from app.logistics.domain.scenario_engine import (
    derive_preferred_adjustment,
    run_logistics_procurement_scenario,
)
from app.logistics.llm.interpretation import (
    build_sanitized_context,
    master_interpretation_service,
    uncalled_interpretation,
)
from app.logistics.schemas.monitoring import snapshot_observed_as_of
from app.logistics.service.agent_read import SnapshotLoadError, load_read

# ---------------------------------------------------------------------------
# SCENARIO_VALIDATION — 시나리오별 판정
# ---------------------------------------------------------------------------


def scenario_validation_reply(request: AgentRequest) -> tuple[AgentReply, ExecutionMetadata]:
    """재무와 달리 스키마 추측이 필요 없다.

    물류는 `PurchaseAgentOutput = PurchaseProposal` 로 매입의 실물 스키마를 그대로
    임포트한다(`app/logistics/schemas/agent.py`). 마스터는 받은 제안을 그 모델로 되살려
    넘기기만 하면 된다 — 이름을 손으로 맞추는 자리가 없으므로 조용히 틀릴 자리도 없다.
    """
    as_of = request.context.as_of
    run_id = logistics_run_id(request)
    tools: list[str] = [T_ARRIVAL, T_CAP, T_RULES]
    # 해석 서비스는 설정만 읽는다 — 여기서 네트워크가 열리지 않는다 (#385).
    # opt-in 이 없으면 enabled=False 인 서비스가 온다. 못 낸 회신에도 이 서비스가
    # 상태 어휘(DISABLED · SKIPPED_TEMPLATE)를 정한다 — 어댑터가 하드코딩하지 않는다.
    llm = master_interpretation_service()

    proposal = as_proposal(request.payload)
    if proposal is None:
        return not_ready_reply(
            request,
            run_id,
            [],
            missing=("purchase_proposal",),
            reason="매입 제안을 물류 입력 모델로 되살리지 못했다",
            llm=uncalled_interpretation(llm),
        )

    # 기준일이 다른 제안은 판정하지 않는다 — 재무 어댑터와 같은 fail-closed (§1.2-6).
    # 스냅샷·Rule 은 요청 `as_of` 로 읽는데 시나리오만 다른 날짜로 계산하면
    # 기준일이 섞인 판정이 READY 로 나간다 (#111).
    if proposal.meta.as_of != as_of:
        reply = AgentReply(
            request_id=request.context.request_id,
            as_of=as_of,
            agent=AGENT,
            mode=request.mode,
            run_id=run_id,
            runtime_status="ERROR",
            business_status="skipped",
            payload={"validation_errors": ["proposal.meta.as_of"]},
            reasoning="Purchase proposal as-of does not match the Master request.",
        )
        return reply, execution_meta(request, run_id, [], llm=uncalled_interpretation(llm))

    try:
        read = load_read(as_of=as_of, sim_run_id=request.context.sim_run_id)
    except SnapshotLoadError:
        return snapshot_error_reply(request, run_id, tools, llm=uncalled_interpretation(llm))
    snapshot = read.snapshot if read is not None else None
    if snapshot is None:
        return not_ready_reply(
            request,
            run_id,
            tools,
            missing=("logistics_snapshot",),
            reason="물류 스냅샷을 읽지 못했다",
            llm=uncalled_interpretation(llm),
        )

    policy = read.policy
    scenario = run_logistics_procurement_scenario(proposal, snapshot)
    rules = evaluate_procurement_rules(as_of=as_of, snapshot=snapshot)
    # 시나리오 집계 ⊕ 하드 제약의 최악값 결합 (마스터 확정 · #121 3단계). 결합하지
    # 않으면 전 시나리오가 reject 인데 하드가 전부 PASS 라고 ok 가 나간다.
    verdict = derive_procurement_verdict(rules, scenario["scenario_results"])

    # 업무 위험 판정(비교식)은 Rule 소유다 (#111 A3). 여기서 계산하는 것이 아니라
    # Rule 이 낸 signal 을 나를 뿐이다.
    tools.append(T_SIGNALS)
    business = evaluate_procurement_business_signals(
        as_of=as_of,
        snapshot=snapshot,
        scenario_results=scenario["scenario_results"],
    )

    cap = scenario["cap_by_date"] if rules["calculation_ready"] else {}
    payload: dict[str, Any] = {
        "verdict": VERDICT_MAP.get(verdict or "", "skipped"),
        "expected_arrival_dates": [d.isoformat() for d in scenario["expected_arrival_dates"]],
        "cap_by_date": {d.isoformat(): to_float(v) for d, v in sorted(cap.items())},
        "hard_constraints": [
            {"code": c.code, "status": c.status, "skip_reason": c.skip_reason}
            for c in rules["hard_constraints"]
        ],
        # Rule 경고 + 업무 위험 signal + 판정 스킵 사실을 한 채널로 싣는다
        # (`merge_business_warnings`).
        # CAPACITY_TIGHT 같은 signal 은 판정을 바꾸지 않지만 Critic 과 사람이 봐야 한다.
        "soft_warnings": merge_business_warnings(rules, business),
        # 시나리오별 판정 상세 (#111 A2) — 총평만으로는 "어떤 시나리오가 왜 conditional
        # 인지"를 마스터가 받지 못한다. 항목 안의 라벨은 봉투 규칙상 근거 면제이고,
        # 숫자(suggested_qty_kg)는 두 겹 안이라 근거 대상이 아니다 (`required_claims`
        # — 배열은 한 겹만 파고든다).
        "scenario_results": [
            {
                "label": result.label,
                "verdict": result.verdict,
                "reason_codes": list(result.reason_codes),
                "adjustments": [
                    {
                        "axis": adjustment.axis,
                        "split_date": adjustment.split_date.isoformat(),
                        # 없는 제안값은 싣지 않는다 — null 로 채우면 "0 제안"과
                        # "제안 없음"이 구분되지 않는다 (§1.2-10)
                        **(
                            {"suggested_qty_kg": to_float(adjustment.suggested_qty_kg)}
                            if adjustment.suggested_qty_kg is not None
                            else {}
                        ),
                        **(
                            {
                                "suggested_arrival_date": (
                                    adjustment.suggested_arrival_date.isoformat()
                                )
                            }
                            if adjustment.suggested_arrival_date is not None
                            else {}
                        ),
                    }
                    for adjustment in result.adjustments
                ],
            }
            for result in scenario["scenario_results"]
        ],
    }

    # Rule 이 낸 조정 제안의 우선 축 (#111 A4) — LLM 이 아니라 Scenario/Rule 의 결정이다.
    # 축이 혼재하거나 0건이면 None 이고, 그때는 키를 싣지 않는다 — 근거 없이 하나를
    # 고르지 않는다 (`derive_preferred_adjustment` docstring).
    preferred = derive_preferred_adjustment(scenario["scenario_results"])
    if preferred is not None:
        payload["preferred_adjustment"] = preferred

    # 품목별 가용재고 — Scenario 엔진이 이미 계산해 돌려준다. 안 실으면 계산한 값을
    # 버리는 것이고(#111 검증 발견 5), 마스터는 판정 회신에서 재고 맥락을 잃는다.
    # `None`(출고 귀속 불명) 위장 금지는 PRE 와 같다 (§1.2-10).
    missing: list[str] = [] if rules["calculation_ready"] else ["cap_by_date"]
    if scenario["inventory_by_item"] is None:
        missing.append("inventory_by_item")
    else:
        payload["inventory_by_item"] = [
            {"item": entry.item, "available_qty_kg": to_float(entry.available_qty_kg)}
            for entry in scenario["inventory_by_item"]
        ]
    # 업무 경고(`business["warnings"]`)는 여기 넣지 않는다. M-1 의
    # `missing_data` 는 마스터가 사용자에게 무엇을 달라고 할지의 이름이고 형식도
    # `logistics_rule/LOG-H02` · `rental_cap_kg@policy_source_ref` 처럼 네임스페이스가
    # 붙은 필드명이다. 맨 경고 코드를 섞으면 어휘가 갈라지고, NOT_READY 로 떨어지는
    # 날 "CAPACITY_TIGHT_POLICY_UNRESOLVED 가 없어 답하지 못했습니다" 라는
    # 이중부정 문장이 나간다 (`master/domain/answer.py` 의 gaps 문구).
    #
    #   사실이 사라지는 것은 아니다 — `soft_warnings` 가 같은 코드를 그대로 나른다.

    # ── 해석 (LLM) — 결정론 결과가 다 선 뒤에만 돈다 (#385) ─────────────
    #
    # 새 계산이 없다. signals · measurements 는 위 `evaluate_procurement_business_signals`
    # 가 낸 것이고, preferred 는 `derive_preferred_adjustment` 가 정한 것이다. Context 에
    # 실리는 것은 signal 코드 · 판정 수치의 확정 표기 · 허용/우선 조정 · 번역된 미확정
    # 이름뿐이고, Lot · 날짜 · kg · 거래처 · 이 payload 는 넘어가지 않는다.
    # missing 원재료는 비-PASS 하드 제약 코드 + Rule 경고 + 판정 스킵 사실이다. M-1
    # `missing_data`(`logistics_rule/LOG-H02` 같은 네임스페이스 이름)를 넘기지 않는다 —
    # 숫자가 든 채로 무숫자 경계를 우회한다.
    # LLM 은 아래 어느 값도 바꾸지 않는다 — verdict · evidences · suggested_adjustments ·
    # preferred_adjustment · missing 은 이 블록 앞에서 이미 확정됐고, 해석은 payload 의
    # 별도 중첩 칸 하나에만 실린다. 실패 · timeout · 검증 탈락은 Template 로 접힌다.
    llm_context, facts_incomplete = build_sanitized_context(
        cycle="PROCUREMENT",
        signals=business["signals"],
        measurements=business["measurements"],
        preferred_adjustment=preferred,
        missing_data=[
            *(c.code for c in rules["hard_constraints"] if c.status != "PASS"),
            *rules["soft_warnings"],
            *business["warnings"],
        ],
    )
    llm_result = llm.interpret(
        llm_context,
        runtime_ready=rules["runtime_status"] == "READY",
        # FAIL 만 차단한다 — UNRESOLVED 는 호출을 막지 않는다 (17-A).
        has_blocking_constraints=any(c.status == "FAIL" for c in rules["hard_constraints"]),
        facts_incomplete=facts_incomplete,
    )
    # 사람이 읽는 해석 — summary · risks · suggested_adjustment. 봉투 검증은 중첩 Mapping 의
    # 값에 근거를 요구하지 않고(`required_claims`), 마스터는 부서 payload 를 파싱하지 않는다.
    payload["interpretation"] = llm_result.interpretation.model_dump(mode="json")

    ref = snapshot_ref(snapshot)
    # verdict 근거의 구성 요소 — 결합 판정에 실제로 들어간 비통과 입력의 수다.
    _failed_hard = len([c for c in rules["hard_constraints"] if c.status != "PASS"])
    _rejected = len([s for s in scenario["scenario_results"] if s.verdict == "reject"])
    _conditional = len([s for s in scenario["scenario_results"] if s.verdict == "conditional"])
    evidences = (
        evidence(
            "verdict",
            _failed_hard + _rejected + _conditional,
            "non_ok_input_count",
            ref,
            "시나리오 집계 ⊕ 하드 제약 최악값 결합 (2026-09-01 확정) — "
            f"비통과 하드 체크 {_failed_hard}건 · reject {_rejected}안 · "
            f"conditional {_conditional}안 → {verdict}",
            source="tool_calc",
        ),
        evidence(
            "cap_by_date",
            len(cap),
            "date_count",
            ref,
            "guaranteed_capacity_kg 에서 도착일별 예상 점유를 뺀 신규 입고 상한 — "
            "1차 MVP 의 Hard Capacity 는 이 하나다",
            source="tool_calc",
        ),
        evidence(
            "expected_arrival_dates",
            snapshot.inbound_lead_days if snapshot.inbound_lead_days is not None else 0,
            "days",
            policy_ref(policy, "inbound_lead_days", ref),
            "매입 분할 회차일 + 입고 리드타임 — 물류 calculate_expected_arrival_dates 산출",
            source="tool_calc",
        ),
    )
    # `soft_warnings` 는 봉투 메타(ENVELOPE_META_KEYS)라 근거 의무는 없지만, PRE 와
    # 같은 이유로 개수를 남긴다 — 판정을 바꾸지 않는 관찰이 몇 건 흘렀는지가 실행
    # 이력에 보여야 Critic 이 잡는다.
    if payload["soft_warnings"]:
        evidences = (
            *evidences,
            evidence(
                "soft_warnings",
                len(payload["soft_warnings"]),
                "warning_count",
                ref,
                "물류 규칙 경고 + 업무 위험 signal + 판정 스킵 사실 — 한 채널로 합류",
                source="tool_calc",
            ),
        )
    if payload.get("inventory_by_item"):
        evidences = (
            *evidences,
            *inventory_by_item_evidences(payload["inventory_by_item"], snapshot),
        )

    judgment_fields: tuple[str, ...] = ("verdict",)
    if preferred is not None:
        # `quantity`/`timing` 은 소문자라 봉투의 대문자 라벨 휴리스틱을 지나친다.
        # 매입 행동을 바꾸는 판정이므로 직접 선언하고 근거를 단다 — envelope 의
        # judgment_fields docstring 이 말하는 바로 그 케이스다 (#111 검증 발견 2).
        judgment_fields = ("verdict", "preferred_adjustment")
        evidences = (
            *evidences,
            evidence(
                "preferred_adjustment",
                len(
                    [
                        adjustment
                        for result in scenario["scenario_results"]
                        # 집계와 같은 모집단 — reject 안의 조정은 근거 건수에서도 뺀다
                        if result.verdict != "reject"
                        for adjustment in result.adjustments
                        if adjustment.axis == preferred
                    ]
                ),
                "adjustment_count",
                ref,
                f"비-reject 시나리오 조정 제안의 고유 축이 {preferred} 하나 — 해당 축 제안 건수",
                source="tool_calc",
            ),
        )

    # M-1 전용 채널 배선 (#111 검증 발견 1) — payload 안에만 두면 마스터 flow 가 세는
    # `reply.suggested_adjustments` 는 0건이고, 사람 화면과 Critic 의 축 침범 검사가
    # 전부 빈 튜플을 본다. 축 어휘는 `_DEPT_AXES["inventory"] = ("quantity","timing")`
    # 과 정확히 같아 추측 없이 싣는다.
    # 두 단계로 나눈다 (#209 · 되먹임 ④). 중복 키를 만난 자리에서 건너뛰면 두 번째
    # 시나리오의 라벨이 사라진다 — 같은 조정이 세 안에서 나와도 첫 라벨 하나만 남는다.
    # 그래서 키별로 라벨을 먼저 모으고, 다 모은 뒤 표준형을 만든다. 중복 제거의 뜻(같은
    # key 는 하나)은 그대로이고 라벨만 합친다.
    collected: dict[tuple[str, date, float], dict[str, Any]] = {}
    for result in scenario["scenario_results"]:
        # reject 안의 adjustment 는 승격하지 않는다 (#121 2단계). multi-split 에서
        # 앞 회차의 조정이 남은 채 전체가 reject 될 수 있는데, 구제 불가 판정한 안의
        # 조정을 행동 제안으로 내보내면 "reject 는 조정으로 구제 불가"와 모순된다.
        # 진단 기록은 payload.scenario_results 에 그대로 남는다 — 사실이 사라지는
        # 것이 아니라 제안으로 격상되지 않을 뿐이다. needs_followup 도 이에 따라
        # reject 만으로는 서지 않는다. 라벨 수집 대상도 아니다.
        if result.verdict == "reject":
            continue
        for adjustment in result.adjustments:
            if adjustment.axis == "quantity" and adjustment.suggested_qty_kg is not None:
                target, unit = to_float(adjustment.suggested_qty_kg), "kg"
                what = f"수량을 {target:g}kg 로 조정 제안"
            elif adjustment.axis == "timing" and adjustment.suggested_arrival_date is not None:
                # 날짜는 float 로 실을 수 없어 as_of 기준 D+N 으로 바꾼다. 사람에게는
                # 손실이 없지만 기계가 읽을 수 있는 형태로는 손실이다 (#209) — 목표 날짜가
                # reason 문자열 안에만 남는다. 그래서 목표 도착일은 아래 reason 에 그대로
                # 남긴다. 절대 날짜 칸이 생기면 그때 문장에서도 뺀다 (마스터가 정해 통보).
                target = float((adjustment.suggested_arrival_date - as_of).days)
                unit = "d"
                what = f"도착일을 {adjustment.suggested_arrival_date.isoformat()} 로 조정 제안"
            else:
                # 값 없는 제안은 전용 채널에 싣지 못한다 — payload.scenario_results 에는
                # 그대로 남아 있어 사실이 사라지지는 않는다.
                continue
            key = (adjustment.axis, adjustment.split_date, target)
            entry = collected.get(key)
            if entry is None:
                # dict 는 삽입 순서를 보존하므로 시나리오 등장 순서가 그대로 남는다.
                collected[key] = {
                    "axis": adjustment.axis,
                    "split_date": adjustment.split_date,
                    "target": target,
                    "unit": unit,
                    "what": what,
                    "labels": [result.label],
                }
            elif result.label not in entry["labels"]:
                entry["labels"].append(result.label)

    # `reason` 에 라벨·회차 앞머리를 넣지 않는다 (미결 §0-6 갈래 ㄱ · 마스터 통보). 같은
    # 사실이 칸과 문장 두 곳에 있으면 한쪽만 고쳐지는 날이 온다 — 화면
    # (`master/domain/answer.py`)은 칸을 읽으므로 라벨과 대상 회차는 칸에 채운다.
    #   빼는 것은 라벨과 대상 회차뿐이다. 목표 도착일은 `what` 안에 남긴다.
    suggested: list[SuggestedAdjustment] = [
        SuggestedAdjustment(
            dept="inventory",
            axis=entry["axis"],
            target_value=entry["target"],
            unit=entry["unit"],
            reason=entry["what"],
            ref_ids=(ref,),
            scenario_labels=tuple(entry["labels"]),
            split_date=entry["split_date"],
        )
        for entry in collected.values()
    ]

    reply = AgentReply(
        request_id=request.context.request_id,
        as_of=as_of,
        agent=AGENT,
        mode=request.mode,
        run_id=run_id,
        runtime_status=rules["runtime_status"],
        business_status=payload["verdict"],
        payload=payload,
        evidences=evidences,
        # 재 봤더니 못 쟀다는 값이다 — 기본값을 그대로 둔 것이 아니다 (#628).
        # 네 Mode 의 회신은 전부 정책값(용량 · 리드타임 · 임계 비율 · 보관한계)을
        # 계산에 넣는데 그 표들에 유효일 칸이 없어, 규칙(§18)대로 결과가 `None` 이다.
        # `as_of` 로 메우지 않는다 — 메우면 «안 쟀다» 가 «쟀다» 로 세어진다.
        observed_at=snapshot_observed_as_of(snapshot),
        suggested_adjustments=tuple(suggested),
        # 조정 제안이 있다는 것은 "이 안 그대로는 안 되고 재검토가 필요하다"다 —
        # 라우팅은 마스터 몫이고 여기서는 사실만 표시한다 (AgentReply docstring).
        needs_followup=bool(suggested),
        judgment_fields=judgment_fields,
        missing_data=tuple(dict.fromkeys(missing)),
        reasoning="매입 시나리오를 물류 관점에서 판정했다.",
    )
    return reply, execution_meta(request, run_id, tools, reply, llm=llm_result)
