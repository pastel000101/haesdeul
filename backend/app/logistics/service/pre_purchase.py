"""PRE_PURCHASE — 매입이 분할 계획을 짤 때 쓰는 창고 경계 제공."""

from __future__ import annotations

from typing import Any

from app.contracts.envelope import AgentReply, AgentRequest, ExecutionMetadata
from app.logistics.domain.agent_evidence import (
    CAP_WINDOW_DAYS,
    JUDGMENT_FIELDS,
    RENTAL_CAP_KEY,
    RENTAL_CAP_KG,
    RENTAL_CAP_REF,
    RULE_PREFIX,
    cap_window,
    evidence,
    free_capacity,
    inventory_by_item_evidences,
    lots_ref_of,
    policies_ref_of,
    policy_ref,
    snapshot_ref,
    to_float,
)
from app.logistics.domain.agent_replies import (
    AGENT,
    T_CAP,
    T_INVENTORY,
    T_LOTS,
    T_RULES,
    execution_meta,
    logistics_run_id,
    not_ready_reply,
    snapshot_error_reply,
)
from app.logistics.domain.rules import evaluate_procurement_rules
from app.logistics.domain.tools import build_inventory_by_item, build_lot_constraints
from app.logistics.schemas.monitoring import snapshot_observed_as_of
from app.logistics.service.agent_read import SnapshotLoadError, load_read

# ---------------------------------------------------------------------------
# PRE_PURCHASE — 경계 제공
# ---------------------------------------------------------------------------


def pre_purchase_reply(request: AgentRequest) -> tuple[AgentReply, ExecutionMetadata]:
    as_of = request.context.as_of
    run_id = logistics_run_id(request)
    tools: list[str] = [T_RULES]

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

    # as_of 대조 — 다른 날의 재고는 그날의 사실이 아니다 (§1.2-6)
    if snapshot.as_of != as_of:
        return not_ready_reply(
            request,
            run_id,
            tools,
            missing=(f"logistics_snapshot@{as_of.isoformat()}",),
            reason=f"물류 스냅샷 기준일이 {snapshot.as_of} 다 — 요청은 {as_of} 다",
        )

    policy = read.policy
    rules = evaluate_procurement_rules(as_of=as_of, snapshot=snapshot)

    # 물류가 못 돌겠다고 한 이유를 그대로 전한다 (2026-08-27 물류 B-1 회신 §5).
    #
    # 물류가 `missing_data` 필드를 새로 만들 필요가 없다. `ConstraintResult` 에
    # `code` · `status` · `skip_reason` 이 이미 있고 어휘도 이미 있다
    # (`IN_TRANSIT_SCHEDULE_UNRESOLVED` 등). 번역이 어댑터 일이다.
    #
    # 이걸 안 옮기면 `runtime_status` 만 NOT_READY 로 오고 왜인지가 안 남는다.
    # 마스터가 사용자에게 무엇을 달라고 할지 모른다 (M-1 §5.1).
    #
    # 그리고 계약상 필요하다 — `RUNTIME_NOT_READY` 는 `missing_data` 가 비면
    # `ContractViolation` 이다. 지금은 다른 이름이 우연히 채워 주고 있을 뿐이라,
    # 그 이름이 사라지면 봉투 검증이 실패한다.
    # `logistics_rule/` 을 앞에 붙인다. 물류 규칙이 말한 것과 어댑터가 payload 를
    # 만들다 못 채운 것을 구분하기 위해서다 — `LOG-H01` 이 UNRESOLVED 면
    # `guaranteed_capacity_kg` 도 비는데, 접두어가 없으면 같은 사실이 두 이름으로
    # 섞여 어느 쪽이 원인인지 흐려진다.
    missing: list[str] = [
        f"{RULE_PREFIX}{c.code}" for c in rules["hard_constraints"] if c.status != "PASS"
    ]
    payload: dict[str, Any] = {}

    # ── 창고 여유 ────────────────────────────────────────────────
    #
    # 뺄셈의 정의를 지어내지 않았다 — 물류 자신의 `calculate_cap_by_date()` 가
    # `free_capacity = guaranteed_capacity_kg − projected_occupancy` 로 쓴다.
    # 확정 입·출고가 없는 as_of 시점의 점유는 `used_capacity_kg` 다.
    #
    # 기준이 `guaranteed`(8,000)이지 `burst`(9,600)가 아닌 것도 물류 코드가 정한
    # 것이다. 페르소나의 6,400kg 은 수요 역산이라 하드 제약으로 못 쓴다
    # (`INVALID_FOR_HARD_N2`) — DB 의 독립 SLA 값을 쓴다.
    free_kg = free_capacity(snapshot)
    if free_kg is None:
        missing.append("guaranteed_capacity_kg")
    else:
        payload["warehouse_free_kg"] = to_float(free_kg)

    # `rental_cap_kg` — 2026-08-27 물류 회신 §1 로 0 확정.
    #
    # DB `agent_policy_config` 에는 이 키가 없다. 값은 쓰되 출처가 DB 가 아니라는 사실을
    # 밝힌다 — 재무 `payroll_date` 가 Schema default 로 조용히 쓰일 수 있는 것과 같은
    # 자리다. 등록되면 이 줄이 저절로 사라진다.
    payload["rental_cap_kg"] = to_float(RENTAL_CAP_KG)
    if RENTAL_CAP_KEY not in policy.source_refs:
        missing.append(f"{RENTAL_CAP_KEY}@policy_source_ref")

    payload.update(
        {
            "used_capacity_kg": to_float(snapshot.used_capacity_kg),
            "cap_by_date_policy": policy.cap_by_date_policy,
            "cap_by_date_window_days": CAP_WINDOW_DAYS,
        }
    )
    for name in (
        "guaranteed_capacity_kg",
        "burst_capacity_kg",
        "daily_inbound_capacity_kg",
        "inbound_transport_capacity_kg",
        "shared_daily_outbound_capacity_kg",
    ):
        value = getattr(snapshot, name)
        if value is None:
            missing.append(name)
        else:
            payload[name] = to_float(value)

    # `inbound_lead_days` 는 위 루프에 넣지 않는다 (#221).
    #
    #   위 다섯은 kg(`Decimal`)이고 이것 하나가 일수(`int`) 다. 한 루프로 묶으면
    #   `to_float()` = `float()` 을 타면서 `2` 가 `2.0` 으로 나간다.
    #
    #       물류 내부   schemas.py  inbound_lead_days: int = Field(ge=0)
    #       봉투        2.0                                         ← 이탈자
    #       IO Contract §3  "inbound_lead_days": 2
    #
    # 받는 쪽도 방어를 둔다 — 마스터의 `master/adapters/critic_bridge.py` `_int_of` 와
    # `master/domain/commitment.py` 의 `lead != int(lead)`, 매입의
    # `purchase_agent/domain/payload.py` 의 `lead != int(lead)`. 생산자가 맞게 보내면 그
    # 방어들은 무해하다 — 지우지는 않는다. 다른 생산자가 붙을 수 있고, 그때도 같은
    # 자리에서 막혀야 한다.
    if snapshot.inbound_lead_days is None:
        missing.append("inbound_lead_days")
    else:
        payload["inbound_lead_days"] = int(snapshot.inbound_lead_days)

    # ── 날짜별 입고 Band ─────────────────────────────────────────
    tools.append(T_CAP)
    cap = cap_window(snapshot, as_of)
    if cap is None:
        # 계산 불능은 빈 dict 로 덮지 않는다 — 0 과 "모름"은 다르다 (§1.2-10)
        missing.append("cap_by_date")
    else:
        payload["cap_by_date"] = {d.isoformat(): to_float(v) for d, v in sorted(cap.items())}

    tools.append(T_LOTS)
    payload["lots"] = [
        {
            "lot_id": lot.lot_id,
            "item": lot.item,
            "available_qty_kg": to_float(lot.available_qty_kg),
            # 신선도는 없을 수 있다 — 0 으로 채우지 않는다 (§1.2-10)
            "remaining_freshness_days": lot.remaining_freshness_days,
            # 물류가 `LotConstraint.grade` 를 나른다 (#77). 매입 등급 배분이 이 값을
            # 본다 — 없으면 필터가 오류 없이 전부 미스로 지나간다.
            #
            # `None` 을 임의 등급으로 채우지 않는다. 현재 `_RAW_GRADE_NORMALIZATION`
            # 이 비어 있어 raw `'상품'` 은 정규화되지 않고 그대로 `None` 이 온다.
            # 그 사실이 payload 에 드러나는 것이 맞다 — 키를 빼면 "물류가 안 준 것"
            # 과 "근거가 없어 못 정한 것" 이 구분되지 않는다 (§1.2-10).
            "grade": lot.grade,
            "status": lot.status,
        }
        for lot in build_lot_constraints(snapshot)
    ]

    # ── 품목별 가용재고 집계 ─────────────────────────────────────
    #
    # Lot 목록과 별개로 싣는다 (#111 A1). 매입/마스터가 Lot 을 재합산하면 가용재고
    # 정의(비-ACTIVE 제외 · 신선도 만료 제외 · 확정 출고 예약분 차감)를 남의 도메인에서
    # 재구현하게 된다 — 집계는 물류 Tool 이 소유한다.
    #
    # `None` 은 Partial Output 이다 — 확정 출고에 item 없는 행이 있으면 임의 배분하지
    # 않고 키를 생략한다. `[]`(품목 0 건 확인)로 위장하지 않는다 (§1.2-10).
    tools.append(T_INVENTORY)
    inventory_by_item = build_inventory_by_item(snapshot)
    if inventory_by_item is None:
        missing.append("inventory_by_item")
    else:
        payload["inventory_by_item"] = [
            {"item": entry.item, "available_qty_kg": to_float(entry.available_qty_kg)}
            for entry in inventory_by_item
        ]

    # ── 품목 보관 정책 ───────────────────────────────────────────
    #
    # Lot 의 `remaining_freshness_days` 와 다른 값이다. 잔여 신선도는 *이미 창고에
    # 있는 그 Lot* 이 앞으로 며칠 쓸 수 있나이고, `operational_limit_days` 는 그 품목을
    # 새로 들일 때 적용할 품목 단위 보관 한계다. 새로 매입할 물량의 기준은 후자라
    # 기존 Lot 의 잔여일수에서 역산하면 안 된다 (`ItemStoragePolicyFact` 참조).
    #
    # 그래서 Lot 목록에서 뽑지 않고 Repository 가 정책 테이블에서 직접 읽은 것을
    # 그대로 나른다 — 재고가 0kg 인 품목의 보관 한계도 매입은 알아야 한다.
    #
    # `None`(미조회)과 `[]`(정책 0 건 확인)은 다르다 (§1.2-10). 미조회면 키를 만들지
    # 않고 이름만 남긴다 — 빈 배열로 덮으면 "정책이 없다" 로 읽힌다.
    policies = snapshot.item_storage_policies
    if policies is None:
        missing.append("item_storage_policies")
    else:
        payload["item_storage_policies"] = [
            {
                "item": policy_fact.item,
                # 값이 없으면 없는 대로 둔다 — 0 이나 기본 계수를 어댑터가 지어내지 않는다.
                "operational_limit_days": policy_fact.operational_limit_days,
                "medium_grade_factor": (
                    None
                    if policy_fact.medium_grade_factor is None
                    else to_float(policy_fact.medium_grade_factor)
                ),
            }
            for policy_fact in policies
        ]

    # 물류가 "돌긴 돌지만 이런 점을 봐 달라" 고 남긴 것. 판정을 바꾸지 않지만
    # 검증 경로에 흘러야 Critic 과 사람이 본다.
    if rules["soft_warnings"]:
        payload["soft_warnings"] = list(rules["soft_warnings"])

    payload["policy_version_used"] = policy.policy_version
    # 정책값이 DB 에서 온 것인지 — 값이 아니라 출처의 문제라 READY 는 유지한다
    missing.extend(
        f"{key}@policy_source_ref"
        for key in (
            "guaranteed_capacity_kg",
            "inbound_lead_days",
            "daily_inbound_capacity_kg",
            "inbound_transport_capacity_kg",
            "cap_by_date_policy",
        )
        if key not in policy.source_refs
    )

    ref = snapshot_ref(snapshot)
    evidences = [
        evidence("used_capacity_kg", snapshot.used_capacity_kg, "kg", ref, "현재 점유량"),
        evidence(
            "cap_by_date_policy",
            CAP_WINDOW_DAYS,
            "days",
            policy_ref(policy, "cap_by_date_policy", ref),
            "확정 입·출고만 반영한다(CONFIRMED_ONLY · N15) — 예정분은 Band 에 안 든다. "
            f"조회 창 D+{CAP_WINDOW_DAYS}",
            grade="SIM_FIXED",
        ),
        evidence(
            RENTAL_CAP_KEY,
            RENTAL_CAP_KG,
            "kg",
            policy_ref(policy, RENTAL_CAP_KEY, RENTAL_CAP_REF),
            "1차 MVP 는 외부 창고 임차 기능이 없다 — 임차 가능량 0 확정. "
            "미확정이 아니다 (2026-08-27 물류 회신 §1)",
            grade="SIM_FIXED",
        ),
        evidence(
            "cap_by_date_window_days",
            CAP_WINDOW_DAYS,
            "days",
            ref,
            "조회 창의 길이. 제약값이 아니라 훑은 범위다 — "
            "이 창 밖의 날짜는 '0' 이 아니라 '안 봤다' 다",
            source="tool_calc",
            grade="SIM_FIXED",
        ),
    ]
    if free_kg is not None:
        evidences.append(
            evidence(
                "warehouse_free_kg",
                free_kg,
                "kg",
                ref,
                "guaranteed_capacity_kg − 현재 점유(as_of 시점). 기준은 독립 SLA "
                "보장치이며 burst 가 아니다. cap_by_date 는 같은 뺄셈을 도착일별 "
                "예상 점유로 다시 하므로 이 값과 일치하지 않는다",
                source="tool_calc",
            )
        )
    for name, unit in (
        ("guaranteed_capacity_kg", "kg"),
        ("burst_capacity_kg", "kg"),
        ("inbound_lead_days", "days"),
        ("daily_inbound_capacity_kg", "kg"),
        ("inbound_transport_capacity_kg", "kg"),
        ("shared_daily_outbound_capacity_kg", "kg"),
    ):
        value = getattr(snapshot, name)
        if value is not None:
            evidences.append(
                evidence(
                    name,
                    value,
                    unit,
                    policy_ref(policy, name, ref),
                    f"Logistics Policy {policy.policy_version}",
                    grade="SIM_FIXED",
                )
            )
    if cap is not None:
        evidences.append(
            evidence(
                "cap_by_date",
                len(cap),
                "date_count",
                ref,
                f"D+{snapshot.inbound_lead_days} 부터 {CAP_WINDOW_DAYS} 일 · "
                "guaranteed_capacity_kg − 도착일별 예상 점유 — 물류 Tool 산출",
                source="tool_calc",
            )
        )
    if payload.get("lots"):
        lots_ref = lots_ref_of(snapshot)
        evidences.append(
            evidence(
                "lots",
                len(payload["lots"]),
                "lot_count",
                lots_ref,
                "현재 on_hand Lot — 등급·신선도 배분 대조용",
            )
        )
        # Lot 안의 숫자마다 근거를 단다 (봉투 v0.3 — 배열 항목 숫자에도 Evidence).
        #
        #   `claim` 에 이름 선택자를 쓴다 — `lots[LOT-...].available_qty_kg`.
        #   번호(`lots[0]`)로 쓰면 Lot 순서가 바뀌는 날 근거가 다른 Lot 을 가리킨다.
        #   `canonical_claim()` 이 항목의 문자열 필드로 찾아 주므로 lot_id 가 안전하다.
        #
        #   중복처럼 보이지만 아니다 — Lot 마다 다른 DB 행이다 (M-25 의 B 안이
        #   문제였던 "같은 근거를 두 벌"과 다르다).
        for lot in payload["lots"]:
            evidences.append(
                evidence(
                    f"lots[{lot['lot_id']}].available_qty_kg",
                    lot["available_qty_kg"],
                    "kg",
                    lots_ref,
                    f"{lot['item']} · 상태 {lot['status']}",
                )
            )
            if lot["remaining_freshness_days"] is not None:
                evidences.append(
                    evidence(
                        f"lots[{lot['lot_id']}].remaining_freshness_days",
                        lot["remaining_freshness_days"],
                        "days",
                        lots_ref,
                        "잔여 신선도 — 등급 배분·소진 순서 판단용",
                    )
                )

    # 보관 정책의 숫자에도 근거를 단다 — 봉투는 배열 항목 안의 숫자마다 Evidence 를
    # 요구한다(`required_claims`). Lot 과 같은 방식으로 이름 선택자를 쓴다:
    # `item_storage_policies[배추].operational_limit_days`. 번호로 쓰면 품목 순서가
    # 바뀌는 날 근거가 다른 품목을 가리킨다.
    #
    # ref 를 지어내지 않는다 — Repository 가 스냅샷 `evidence_refs` 에 실어 둔
    # `DB:item_storage_policies` 를 그대로 가리킨다.
    #
    # 값이 `None` 인 필드는 근거를 만들지 않는다. 봉투도 숫자가 아닌 값에는 근거를
    # 요구하지 않는다 — 없는 값에 근거를 붙이면 "확인했다" 는 거짓이 된다.
    if payload.get("item_storage_policies"):
        policies_ref = policies_ref_of(snapshot)
        for row in payload["item_storage_policies"]:
            if row["operational_limit_days"] is not None:
                evidences.append(
                    evidence(
                        f"item_storage_policies[{row['item']}].operational_limit_days",
                        row["operational_limit_days"],
                        "days",
                        policies_ref,
                        f"{row['item']} 품목의 신규 입고분 운영 보관한계 — "
                        "기존 Lot 의 잔여 신선도와 다른 값이다",
                    )
                )
            if row["medium_grade_factor"] is not None:
                evidences.append(
                    evidence(
                        f"item_storage_policies[{row['item']}].medium_grade_factor",
                        row["medium_grade_factor"],
                        "ratio",
                        policies_ref,
                        f"{row['item']} 중등급 보관한계 계수 — DB Fact 를 그대로 나른다",
                    )
                )

    if payload.get("inventory_by_item"):
        evidences.extend(inventory_by_item_evidences(payload["inventory_by_item"], snapshot))

    # 물류가 NOT_READY 를 냈는데 이름이 하나도 없으면 계약 위반이다
    # (M-1 §5.1 — 봉투가 ContractViolation 을 던진다).
    #
    #    `rules["runtime_status"]` 는 물류가 정하고 `missing` 은 어댑터가 따로 모은다.
    #    둘이 어긋날 수 있다 — 물류 Rule 이 막았는데 어댑터가 읽은 값은 다 멀쩡한 경우다.
    #    지금은 `rental_cap_kg@policy_source_ref` 가 늘 들어 있어 우연히 안 비어 있지만,
    #    DB 에 그 키가 등록되면 비게 되고, 그러면 물류 어댑터가 예외로 실패한다.
    #
    #    통과 못 한 하드 체크의 코드를 그대로 적는다 — 지어내지 않고 물류가 낸 이름이다.
    # 비-PASS 코드는 `missing` 초기화가 이미 같은 comprehension 으로 무조건 채우므로
    # 여기서 다시 모으지 않는다. `logistics_runtime` 만이 유효한 최후 방어다: Rule 이
    # 막았는데 비-PASS 코드가 하나도 없는(=이름을 못 내는) 경우에 사실만이라도 남긴다.
    if rules["runtime_status"] != "READY" and not missing:
        missing.append("logistics_runtime")

    if payload.get("soft_warnings"):
        evidences.append(
            evidence(
                "soft_warnings",
                len(payload["soft_warnings"]),
                "warning_count",
                ref,
                "물류 규칙이 남긴 관찰 — 판정을 바꾸지 않지만 사람과 Critic 이 본다",
                source="tool_calc",
            )
        )

    reply = AgentReply(
        request_id=request.context.request_id,
        as_of=as_of,
        agent=AGENT,
        mode=request.mode,
        run_id=run_id,
        runtime_status=rules["runtime_status"],
        business_status="ok" if rules["runtime_status"] == "READY" else "skipped",
        payload=payload,
        evidences=tuple(evidences),
        # 재 봤더니 못 쟀다는 값이다 — 기본값을 그대로 둔 것이 아니다 (#628).
        # 네 Mode 의 회신은 전부 정책값(용량 · 리드타임 · 임계 비율 · 보관한계)을
        # 계산에 넣는데 그 표들에 유효일 칸이 없어, 규칙(§18)대로 결과가 `None` 이다.
        # `as_of` 로 메우지 않는다 — 메우면 «안 쟀다» 가 «쟀다» 로 세어진다.
        observed_at=snapshot_observed_as_of(snapshot),
        judgment_fields=JUDGMENT_FIELDS,
        missing_data=tuple(dict.fromkeys(missing)),
        reasoning="물류 경계를 산출했다.",
    )
    return reply, execution_meta(request, run_id, tools, reply)
