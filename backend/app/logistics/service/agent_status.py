"""STATUS_QUERY — "지금 창고 · 재고 상황" 조회 (개요형 · 질문형)."""

from __future__ import annotations

import logging
from typing import Any

from app.contracts.envelope import AgentReply, AgentRequest, ExecutionMetadata
from app.logistics.domain.agent_evidence import (
    evidence,
    free_capacity,
    lots_ref_of,
    policies_ref_of,
    policy_ref,
    snapshot_ref,
    to_float,
)
from app.logistics.domain.agent_replies import (
    AGENT,
    T_FRESHNESS,
    T_LOTS,
    T_USAGE,
    execution_meta,
    logistics_run_id,
    not_ready_reply,
    snapshot_error_reply,
)
from app.logistics.domain.rules import measure_freshness_facts
from app.logistics.domain.tools import build_lot_constraints, calculate_window_capacity_usage
from app.logistics.schemas.monitoring import snapshot_observed_as_of
from app.logistics.service.agent_read import SnapshotLoadError, load_read
from app.logistics.service.status_question import answer_status_question

# ---------------------------------------------------------------------------
# STATUS_QUERY — "지금 창고·재고 상황" 조회
# ---------------------------------------------------------------------------


def status_query_reply(request: AgentRequest) -> tuple[AgentReply, ExecutionMetadata]:
    """묻기만 하는 요청. 경계가 아니라 상태를 돌려준다.

    `PRE_PURCHASE` 와 읽는 것은 같고 싣는 것이 다르다. `cap_by_date` ·
    `inbound_lead_days` · `daily_inbound_capacity_kg` 는 매입이 분할 계획을 짤 때 쓰는
    경계라, "지금 창고 어떠냐" 를 묻는 사람에게는 답이 아니다. D+18 Band 를 조회 답에
    실으면 사람이 읽을 것이 아닌 표가 화면을 덮는다.

    하드 제약 위반이 조회를 막지 않는다. `PRE_PURCHASE` 는 `LOG-H01` 이 UNRESOLVED 면
    경계를 못 내지만, 조회는 실행으로 이어지지 않는다. 규칙이 못 본 것은 `missing_data` 로
    이름만 밝히고 읽어낸 상태는 답한다 (§3.7.6 — 못 한 것을 한 척하지 않되, 할 수 있는
    것을 안 한 척도 하지 않는다).

    `lots` 를 통째로 싣지 않는다. 조회에 필요한 것은 "몇 건이 얼마나 있고 임박한 것이
    있는가" 이지 Lot 목록이 아니다 — 목록은 매입이 배분할 때 쓴다.

    운영 Fact 를 함께 싣는다 (#396). 사용률·신선도 비율과 그 임계 정책값을 나란히 두어
    사람이 스스로 판단하게 한다.

      ```text
      capacity_window_usage_ratio      Tool 산출 — 창 안에서 가장 빡빡한 날의 사용률
      capacity_tight_ratio             정책 원값 (PROVISIONAL)
      freshness_min_remaining_ratio    Tool 산출 — 셈할 수 있었던 ACTIVE Lot 의 최소 비율
      freshness_pressure_ratio         정책 원값 (PROVISIONAL)
      freshness_risk_lot_count         Rule 비교식 재사용 — 임계 이하 Lot 수
      freshness_unresolved_lot_count   잔여·한계 미확인 Lot 수 (0 이 아니라 '모른다')
      freshness_expired_lot_count      잔여 0 이하로 확인된 Lot 수
      ```

      재기만 하고 판정하지 않는다. 임계를 실어도 비교는 하지 않는다 —
      `CAPACITY_TIGHT` · `INVENTORY_FRESHNESS_PRESSURE` · `FRESHNESS_QUALITY_RISK` 는 Rule
      소유 signal 이고 조회는 그것도 `judgment_fields` 도 내지 않는다.

      `freshness_expired_lot_count` 는 폐기 대상 수가 아니다. 폐기대기 판정과 실행은
      `turnover` · `disposal` 소유이고 사람이 확정한다 — 어댑터는 그 모듈을 부르지도,
      `disposal_candidate` 어휘를 쓰지도 않는다.

      신선도 넷의 모집단은 `ACTIVE` Lot 이라 위 `lot_count` 와 다르다. `lot_count` 는
      창고에 남아 있는 Lot 전부다 — 비-ACTIVE 도 반출 전이면 공간을 차지하므로
      Repository 가 status 로 거르지 않는다. 두 수의 합은 맞지 않는다.

    질문형 경로 (Issue #789 · LOG-MDS-004): `payload={"question": ...}` 이면 자연어
    조회다 — Overview 대신 `answer_status_question` 이 topics/scope/items 를 해석하고
    Read-only Tool 을 조합해 답한다. `payload={}` (또는 질문 없음)이면 아래 Overview 를
    탄다.
    """
    question = str(request.payload.get("question") or "").strip()
    if question:
        return status_question_reply(request, question)

    as_of = request.context.as_of
    run_id = logistics_run_id(request)
    tools: list[str] = [T_LOTS]

    try:
        read = load_read(as_of=as_of, sim_run_id=request.context.sim_run_id)
    except SnapshotLoadError:
        return snapshot_error_reply(request, run_id, tools)
    snapshot = read.snapshot if read is not None else None
    if snapshot is None:
        return not_ready_reply(
            request,
            run_id,
            tools,
            missing=("logistics_snapshot", "logistics_runtime_fixture"),
            reason="물류 스냅샷을 읽지 못했다",
        )
    if snapshot.as_of != as_of:
        return not_ready_reply(
            request,
            run_id,
            tools,
            missing=(f"logistics_snapshot@{as_of.isoformat()}",),
            reason=f"물류 스냅샷 기준일이 {snapshot.as_of} 다 — 요청은 {as_of} 다",
        )

    policy = read.policy
    ref = snapshot_ref(snapshot)
    lots_ref = lots_ref_of(snapshot)
    # 신선도 비율의 분모(유효 보관한계)는 품목 정책에서 왔다 — Lot 참조만 달면
    # "이 비율이 어디서 왔나" 를 따라갔을 때 분모의 출처가 빠진다 (PRE_SALES 와 같은 규율).
    policies_ref = policies_ref_of(snapshot)
    freshness_refs = (policies_ref,) if policies_ref != lots_ref else ()
    lots = build_lot_constraints(snapshot)

    missing: list[str] = []
    payload: dict[str, Any] = {
        "as_of": as_of.isoformat(),
        "used_capacity_kg": to_float(snapshot.used_capacity_kg),
        "lot_count": len(lots),
    }

    free_kg = free_capacity(snapshot)
    if free_kg is None:
        # 0 과 "모름" 은 다르다 (§1.2-10) — 여유를 못 셈했으면 이름을 밝힌다
        missing.append("guaranteed_capacity_kg")
    else:
        payload["warehouse_free_kg"] = to_float(free_kg)

    # 여유만 싣고 총량을 빼면 "7,499kg 남았다" 가 큰 건지 작은 건지 알 수 없다.
    # 실으면 근거도 같이 달아야 한다 — 봉투가 E-EVIDENCE-MISSING 으로 잡는다.
    guaranteed = snapshot.guaranteed_capacity_kg
    if guaranteed is not None:
        payload["guaranteed_capacity_kg"] = to_float(guaranteed)

    # ── 임박 신선도 ──────────────────────────────────────────────
    #
    # 임계를 지어내지 않는다. "며칠 이하가 임박인가" 는 물류 정책이지 조회의 판단이
    # 아니라서, 최솟값과 그 Lot 만 밝히고 위험 여부는 사람이 본다. 여기서 3일·5일 같은
    # 수를 고르면 §1.2-8(하드 제약값 파생 금지)을 어긴다.
    fresh = [
        (lot.remaining_freshness_days, lot.lot_id)
        for lot in lots
        if lot.remaining_freshness_days is not None
    ]
    if fresh:
        days, lot_id = min(fresh)
        payload["min_remaining_freshness_days"] = days
        payload["min_freshness_lot_id"] = lot_id
    elif lots:
        # Lot 은 있는데 신선도가 하나도 안 실렸다 — 빈 값으로 덮지 않는다
        missing.append("lots[].remaining_freshness_days")

    # ── 창고 사용률 (측정) ───────────────────────────────────────
    #
    # 여기서도 임계를 지어내지 않는다 (#396). 등록된 정책값을 그대로 나를 뿐 어댑터가
    # 기준을 고르지 않는다. 사용률과 임계를 나란히 싣되 비교는 하지 않는다:
    # `CAPACITY_TIGHT` 는 Rule 소유 signal 이고 조회는 그것을 내지 않는다.
    #
    # `cap_by_date` 표는 싣지 않는다. 사람이 읽을 것은 "가장 빡빡한 날이 몇 %" 한 값이고,
    # 창의 정의(시작일·길이)는 Tool 소유라 어댑터가 다시 나열하지 않는다.
    tools.append(T_USAGE)
    usage = calculate_window_capacity_usage(snapshot, as_of)
    if usage is None:
        # 리드타임·확정 일정이 없어 창을 못 세운 것이다. `PRE_PURCHASE` 는 같은 사실을
        # hard_constraints 로 나르지만 조회에는 그 칸이 없어 여기서 이름을 밝힌다.
        missing.append("capacity_window_usage_ratio")
    else:
        payload["capacity_window_usage_ratio"] = to_float(usage)

    capacity_tight_ratio = snapshot.capacity_tight_ratio
    if capacity_tight_ratio is None:
        # 선택 정책 미등재 — 값을 지어내지 않는다. "기준이 없어 못 쟀다" 와
        # "재 봤더니 안전하다" 는 다르다 (LLM 정책 결정서 §4 와 같은 태도).
        missing.append("capacity_tight_ratio")
    else:
        payload["capacity_tight_ratio"] = to_float(capacity_tight_ratio)
        if "capacity_tight_ratio" not in policy.source_refs:
            missing.append("capacity_tight_ratio@policy_source_ref")

    # ── 신선도 측정 ─────────────────────────────────────────────
    #
    # 모집단이 위 `lot_count` 와 다르다. 아래 넷은 전부 ACTIVE Lot 만 보고
    # (`schemas/vocabulary.ACTIVE_LOT_STATUS`), `lot_count` 는 창고에 남아 있는 Lot
    # 전부다 — 비-ACTIVE(격리·검수)도 반출 전이면 공간을 차지하므로 Repository 가 status
    # 로 거르지 않는다. 두 수의 합이 맞지 않는 것이 정상이라 근거 문장에 모집단을 적는다.
    #
    # 비교식을 어댑터가 만들지 않는다. 위험 Lot 수의 `<= 임계` 는
    # `rules.count_freshness_risk_lots` 소유이고 signal 판정이 쓰는 그 함수다. 여기서 다시
    # 세면 "signal 은 섰는데 위험 Lot 0건" 이 성립할 수 있다.
    tools.append(T_FRESHNESS)
    freshness = measure_freshness_facts(snapshot=snapshot)
    # 건수는 `int` 로 둔다. `to_float()`(= `float()`) 을 태우면 `3` 이 `3.0` 으로 나간다
    # (#221 의 `inbound_lead_days` 와 같은 경로).
    payload["freshness_unresolved_lot_count"] = freshness["freshness_unresolved_lot_count"]
    payload["freshness_expired_lot_count"] = freshness["freshness_expired_lot_count"]

    min_freshness_ratio = freshness.get("freshness_min_remaining_ratio")
    if min_freshness_ratio is None:
        # 비율을 셈할 수 있는 ACTIVE Lot 이 하나도 없다 — 0 으로 덮지 않는다.
        missing.append("freshness_min_remaining_ratio")
    else:
        payload["freshness_min_remaining_ratio"] = to_float(min_freshness_ratio)

    freshness_risk_lot_count = freshness.get("freshness_risk_lot_count")
    if freshness_risk_lot_count is None:
        # 임계 정책이 없어 세지 않았다. 기준 없는 `0` 은 "확인했고 없음" 으로 읽힌다.
        missing.append("freshness_risk_lot_count")
    else:
        payload["freshness_risk_lot_count"] = freshness_risk_lot_count

    freshness_pressure_ratio = snapshot.freshness_pressure_ratio
    if freshness_pressure_ratio is None:
        missing.append("freshness_pressure_ratio")
    else:
        payload["freshness_pressure_ratio"] = to_float(freshness_pressure_ratio)
        if "freshness_pressure_ratio" not in policy.source_refs:
            missing.append("freshness_pressure_ratio@policy_source_ref")

    evidences = [
        evidence("used_capacity_kg", snapshot.used_capacity_kg, "kg", ref, "현재 점유량"),
        evidence("lot_count", len(lots), "count", lots_ref, "ACTIVE Lot 건수"),
    ]
    if guaranteed is not None:
        evidences.append(
            evidence(
                "guaranteed_capacity_kg",
                guaranteed,
                "kg",
                policy_ref(policy, "guaranteed_capacity_kg", ref),
                "3PL 보장 Capacity (독립 SLA) — burst 9,600 은 순간 초과라 기준이 아니다",
                grade="SIM_FIXED",
            )
        )
    if free_kg is not None:
        evidences.append(
            evidence(
                "warehouse_free_kg",
                free_kg,
                "kg",
                ref,
                "guaranteed_capacity_kg − 현재 물리 점유량(as_of 시점)",
            )
        )
    if "min_remaining_freshness_days" in payload:
        evidences.append(
            evidence(
                "min_remaining_freshness_days",
                payload["min_remaining_freshness_days"],
                "days",
                lots_ref,
                f"Lot {payload['min_freshness_lot_id']} — 가장 짧은 잔여 신선도",
            )
        )

    # ── 운영 Fact 근거 (#396) ────────────────────────────────────
    #
    # 넷은 Tool 산출(`tool_calc`)이고 둘은 정책 원값(`SIM_FIXED`)이다. 표기를 나누는
    # 이유는 읽는 사람이 "잰 값" 과 "기준" 을 구별해야 하기 때문이다.
    if "capacity_window_usage_ratio" in payload:
        evidences.append(
            evidence(
                "capacity_window_usage_ratio",
                payload["capacity_window_usage_ratio"],
                "ratio",
                ref,
                "고정 조회 창에서 가장 빡빡한 날의 창고 사용률 — "
                "1 − (창 내 최소 여유 ÷ 보장 Capacity). 확정 입·출고만 반영하며 "
                "임계와 비교하지 않는다",
                source="tool_calc",
            )
        )
    if "capacity_tight_ratio" in payload:
        evidences.append(
            evidence(
                "capacity_tight_ratio",
                payload["capacity_tight_ratio"],
                "ratio",
                policy_ref(policy, "capacity_tight_ratio", ref),
                f"창고 압박 판정 임계 정책값 ({policy.policy_version}) — 실업계 기준이 아니라 "
                "시뮬레이션·Agent 검증용 PROVISIONAL 운영값이다. 이 회신은 나란히 싣기만 "
                "하고 비교하지 않는다",
                grade="SIM_FIXED",
            )
        )

    evidences.append(
        evidence(
            "freshness_unresolved_lot_count",
            payload["freshness_unresolved_lot_count"],
            "count",
            lots_ref,
            "ACTIVE Lot 중 잔여 신선도 또는 유효 보관한계를 확인하지 못해 비율 계산에서 "
            "뺀 건수 — 위 lot_count 와 모집단이 다르고, 0 취급이 아니라 '모른다' 의 건수다",
            source="tool_calc",
            extra_ref_ids=freshness_refs,
        )
    )
    evidences.append(
        evidence(
            "freshness_expired_lot_count",
            payload["freshness_expired_lot_count"],
            "count",
            lots_ref,
            "ACTIVE Lot 중 잔여 신선도가 0 이하로 확인된 건수 — 판매 가용에서 빠지는 상태 "
            "사실이다. 폐기 판정도 폐기 대상 수도 아니며, 폐기는 turnover·disposal 이 "
            "소유하고 사람이 확정한다",
            source="tool_calc",
            extra_ref_ids=freshness_refs,
        )
    )
    if "freshness_min_remaining_ratio" in payload:
        evidences.append(
            evidence(
                "freshness_min_remaining_ratio",
                payload["freshness_min_remaining_ratio"],
                "ratio",
                lots_ref,
                "비율을 셈할 수 있었던 ACTIVE Lot 의 최소 잔여 비율 — "
                "잔여 신선도 ÷ 유효 보관한계. 분모는 중 등급 계수가 반영된 값이라 "
                "품목 보관 정책 원값과 다를 수 있다",
                source="tool_calc",
                extra_ref_ids=freshness_refs,
            )
        )
    if "freshness_risk_lot_count" in payload:
        evidences.append(
            evidence(
                "freshness_risk_lot_count",
                payload["freshness_risk_lot_count"],
                "count",
                lots_ref,
                "비율을 셈할 수 있었던 ACTIVE Lot 중 잔여 비율이 정책 임계 이하인 건수 — "
                "Rule 이 소유한 비교식을 그대로 쓴다. 이 건수는 signal 을 만들지 않는다",
                source="tool_calc",
                extra_ref_ids=freshness_refs,
            )
        )
    if "freshness_pressure_ratio" in payload:
        evidences.append(
            evidence(
                "freshness_pressure_ratio",
                payload["freshness_pressure_ratio"],
                "ratio",
                policy_ref(policy, "freshness_pressure_ratio", ref),
                f"신선도 압박 판정 임계 정책값 ({policy.policy_version}) — 실업계 기준이 아니라 "
                "시뮬레이션·Agent 검증용 PROVISIONAL 운영값이다",
                grade="SIM_FIXED",
            )
        )

    # policy 는 항상 있다 — Snapshot 조립이 Policy 로 만들어지므로 read 가 성공한 순간
    # 둘 다 존재한다 (#121 ⑤).
    #
    # policy 를 따로 다시 조회하고 예외를 삼키는 분기를 두지 않는다. 그러면 "snapshot 은
    # 있는데 policy 는 None" 이 성립하고, `cap_by_date_policy="UNKNOWN"` 같은 지어낸 값이
    # 판정 필드(`judgment_fields`)에 실려 근거까지 붙은 채 봉투 검증을 통과한다
    # (§1.2-10 위반).
    payload["policy_version_used"] = policy.policy_version
    if "guaranteed_capacity_kg" not in policy.source_refs:
        missing.append("guaranteed_capacity_kg@policy_source_ref")

    reply = AgentReply(
        request_id=request.context.request_id,
        as_of=as_of,
        agent=AGENT,
        mode=request.mode,
        run_id=run_id,
        runtime_status="READY",
        business_status="ok",
        payload=payload,
        evidences=tuple(evidences),
        # 재 봤더니 못 쟀다는 값이다 — 기본값을 그대로 둔 것이 아니다 (#628).
        # 네 Mode 의 회신은 전부 정책값(용량 · 리드타임 · 임계 비율 · 보관한계)을 계산에
        # 넣는데 그 표들에 유효일 칸이 없어, 규칙(§18)대로 결과가 `None` 이다.
        # `as_of` 로 메우지 않는다 — 메우면 «안 쟀다» 가 «쟀다» 로 세어진다.
        observed_at=snapshot_observed_as_of(snapshot),
        # 조회는 판정을 내지 않는다 — cap_by_date_policy 는 경계 해석이라 여기 없다
        judgment_fields=(),
        missing_data=tuple(missing),
        reasoning="현재 창고·재고 상태를 조회했다."
        if not missing
        else "읽어낸 상태는 답했고, 채우지 못한 값은 이름을 밝혔다.",
    )
    return reply, execution_meta(request, run_id, tools, reply)


# ---------------------------------------------------------------------------
# STATUS_QUERY — 질문형 (자연어 조회 · Issue #789 · LOG-MDS-004)
# ---------------------------------------------------------------------------

#: 질문형 조회가 부를 수 있는 Read-only Tool (관측용 — DeptMeta 는 조회라 안 붙는다).
STATUS_QUESTION_TOOLS: tuple[str, ...] = (
    "get_item_lots",
    "get_sales_commitments",
    "get_capacity_context",
    "get_inbound_schedule",
    "get_open_exceptions",
    "get_policy",
)


def status_question_reply(
    request: AgentRequest, question: str
) -> tuple[AgentReply, ExecutionMetadata]:
    """자연어 STATUS_QUERY. 해석·resolver·Tool 조합은 `service/status_question.py` 소유다.

    어댑터는 question 을 넘기고 결과를 봉투로 감쌀 뿐이다 — 품목 파싱도 숫자 계산도
    여기서 하지 않는다 (Master 가 question 만 넘기는 것과 같은 규율).
    """
    run_id = logistics_run_id(request)
    tools = list(STATUS_QUESTION_TOOLS)
    try:
        answer = answer_status_question(
            question=question,
            sim_run_id=request.context.sim_run_id,
            as_of=request.context.as_of,
        )
    except Exception:
        logging.getLogger(__name__).exception("STATUS_QUERY 질문형 조회 실패")
        return snapshot_error_reply(request, run_id, tools)

    reply = AgentReply(
        request_id=request.context.request_id,
        as_of=request.context.as_of,
        agent=AGENT,
        mode=request.mode,
        run_id=run_id,
        runtime_status="READY",
        business_status="ok",
        payload=dict(answer.payload),
        # 숫자를 한 Mapping 아래 접었으므로 최상위 근거 요구가 없다 — 판정도 없다.
        evidences=(),
        # 여러 Tool·여러 축의 관측일을 한 값으로 대표하지 않는다 (안 쟀다).
        observed_at=None,
        judgment_fields=(),
        missing_data=answer.missing_data,
        reasoning=answer.reasoning or "질문형 물류 상태를 조회했다.",
    )
    return reply, execution_meta(request, run_id, tools, reply)
