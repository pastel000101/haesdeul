"""업무 위험 signal 3종·preferred·missing_data 채널 — LLM 정책 결정서 §3~§5 검증."""

from datetime import date
from decimal import Decimal

from app.contracts.envelope import AgentRequest, ExecutionContext
from app.logistics import adapter
from app.logistics.domain.rules import (
    CAPACITY_TIGHT,
    CAPACITY_TIGHT_POLICY_UNRESOLVED,
    FRESHNESS_PRESSURE_POLICY_UNRESOLVED,
    FRESHNESS_QUALITY_RISK,
    INVENTORY_FRESHNESS_PRESSURE,
    LOT_FRESHNESS_UNRESOLVED,
    SCENARIO_ADJUSTMENT_REQUIRED,
    evaluate_procurement_business_signals,
    evaluate_sales_business_signals,
    measure_freshness_facts,
)
from app.logistics.domain.scenario_engine import derive_preferred_adjustment
from app.logistics.domain.tools import (
    calculate_window_capacity_usage,
    collect_freshness_lot_census,
    collect_freshness_pressure_inputs,
)
from app.logistics.schemas.agent import ScenarioAdjustment, ScenarioValidationResult
from app.logistics.schemas.current import LogisticsRead
from app.logistics.schemas.snapshot import POLICY_VERSION, InventoryLotSnapshot, LogisticsPolicy
from tests.logistics.mode_modules import swap_in_modes

AS_OF = date(2026, 8, 21)


def _lot(**overrides) -> InventoryLotSnapshot:
    lot = {
        "lot_id": "LOT-001",
        "item": "배추",
        "grade": None,
        "available_qty_kg": Decimal(500),
        "remaining_freshness_days": 8,
        "effective_freshness_limit_days": 10,
        "status": "ACTIVE",
        "storage_zone": "COLD_HUMID",
    }
    lot.update(overrides)
    # model_copy(update=) 는 dict 를 모델로 강제 변환하지 않으므로 모델로 만든다.
    return InventoryLotSnapshot(**lot)


def _scenario(verdict: str, adjustments: list[ScenarioAdjustment]) -> ScenarioValidationResult:
    return ScenarioValidationResult(
        label="기본", verdict=verdict, reason_codes=[], adjustments=adjustments
    )


def _quantity_adjustment() -> ScenarioAdjustment:
    return ScenarioAdjustment(axis="quantity", split_date=AS_OF, suggested_qty_kg=Decimal(100))


def _timing_adjustment() -> ScenarioAdjustment:
    return ScenarioAdjustment(
        axis="timing", split_date=AS_OF, suggested_arrival_date=date(2026, 8, 25)
    )


# ---------------------------------------------------------------------------
# CAPACITY_TIGHT — 고정 18일 창 · guaranteed 분모 · 경계 포함
# ---------------------------------------------------------------------------


def test_window_usage_uses_fixed_window_not_proposal_dates(complete_logistics_snapshot):
    """점유 1,000 / guaranteed 8,000 — 제안 없이도 창 사용률이 나온다."""
    usage = calculate_window_capacity_usage(complete_logistics_snapshot, AS_OF)

    assert usage == Decimal("0.125")


def test_capacity_tight_fires_at_boundary_inclusive(complete_logistics_snapshot):
    snapshot = complete_logistics_snapshot.model_copy(
        update={
            "used_capacity_kg": Decimal(7200),  # 7200/8000 = 정확히 0.90
            "capacity_tight_ratio": Decimal("0.90"),
        }
    )

    result = evaluate_procurement_business_signals(
        as_of=AS_OF, snapshot=snapshot, scenario_results=[]
    )

    assert CAPACITY_TIGHT in result["signals"]


def test_capacity_below_threshold_stays_silent(complete_logistics_snapshot):
    snapshot = complete_logistics_snapshot.model_copy(
        update={
            "used_capacity_kg": Decimal(7100),
            "capacity_tight_ratio": Decimal("0.90"),
        }
    )

    result = evaluate_procurement_business_signals(
        as_of=AS_OF, snapshot=snapshot, scenario_results=[]
    )

    assert CAPACITY_TIGHT not in result["signals"]


def test_missing_capacity_policy_skips_with_warning_not_silence(complete_logistics_snapshot):
    """정책값 부재 = 판정 SKIPPED + 경고. 100% 점유여도 판정을 지어내지 않는다."""
    snapshot = complete_logistics_snapshot.model_copy(
        update={"used_capacity_kg": Decimal(8000), "capacity_tight_ratio": None}
    )

    result = evaluate_procurement_business_signals(
        as_of=AS_OF, snapshot=snapshot, scenario_results=[]
    )

    assert CAPACITY_TIGHT not in result["signals"]
    assert CAPACITY_TIGHT_POLICY_UNRESOLVED in result["warnings"]


# ---------------------------------------------------------------------------
# INVENTORY_FRESHNESS_PRESSURE — 유효 한계 분모 · grade=None 포함 · None 제외
# ---------------------------------------------------------------------------


def test_freshness_pressure_fires_at_boundary_inclusive(complete_logistics_snapshot):
    snapshot = complete_logistics_snapshot.model_copy(
        update={
            "on_hand_by_lot": [_lot(remaining_freshness_days=3, effective_freshness_limit_days=10)],
            "freshness_pressure_ratio": Decimal("0.30"),  # 3/10 = 정확히 0.30
        }
    )

    result = evaluate_procurement_business_signals(
        as_of=AS_OF, snapshot=snapshot, scenario_results=[]
    )

    assert INVENTORY_FRESHNESS_PRESSURE in result["signals"]


def test_freshness_pressure_includes_grade_none_lots(complete_logistics_snapshot):
    """grade=None 은 제외 사유가 아니다 — 현재 전 Lot 이 None 이라 빼면 신호가 죽는다."""
    snapshot = complete_logistics_snapshot.model_copy(
        update={
            "on_hand_by_lot": [
                _lot(grade=None, remaining_freshness_days=2, effective_freshness_limit_days=10)
            ],
            "freshness_pressure_ratio": Decimal("0.30"),
        }
    )

    result = evaluate_procurement_business_signals(
        as_of=AS_OF, snapshot=snapshot, scenario_results=[]
    )

    assert INVENTORY_FRESHNESS_PRESSURE in result["signals"]


def test_effective_limit_is_the_denominator_not_operational_raw():
    """중 등급: remaining 6 / 유효 한계 6 = 1.0 — 원값 10 으로 나누면 0.6 이 된다."""
    from app.logistics.schemas.snapshot import InventoryLogisticsSnapshot

    snapshot = InventoryLogisticsSnapshot(
        snapshot_id=None,
        as_of=AS_OF,
        on_hand_by_lot=[
            _lot(grade="중", remaining_freshness_days=6, effective_freshness_limit_days=6)
        ],
        in_transit=[],
        confirmed_inbound_schedule=[],
        confirmed_outbound_schedule=[],
        outbound_commitments=[],
        used_capacity_kg=Decimal(500),
        guaranteed_capacity_by_zone_kg=None,
        evidence_refs=[],
    )

    ratios, unresolved = collect_freshness_pressure_inputs(snapshot)

    assert ratios == [Decimal(1)]
    assert unresolved == 0


def test_unresolved_freshness_lot_is_excluded_and_surfaced(complete_logistics_snapshot):
    """remaining=None → 0 취급도 위험 강제도 없이 제외하되, 제외 사실은 경고로 남는다."""
    snapshot = complete_logistics_snapshot.model_copy(
        update={
            "on_hand_by_lot": [
                _lot(remaining_freshness_days=None, effective_freshness_limit_days=None)
            ],
            "freshness_pressure_ratio": Decimal("0.30"),
        }
    )

    result = evaluate_procurement_business_signals(
        as_of=AS_OF, snapshot=snapshot, scenario_results=[]
    )

    assert INVENTORY_FRESHNESS_PRESSURE not in result["signals"]
    assert LOT_FRESHNESS_UNRESOLVED in result["warnings"]


def test_non_active_lots_do_not_join_freshness_pressure(complete_logistics_snapshot):
    """격리·검수 재고의 신선도는 매입 압박 신호에 섞지 않는다 — 대상은 가용 재고다."""
    snapshot = complete_logistics_snapshot.model_copy(
        update={
            "on_hand_by_lot": [
                _lot(
                    status="QUARANTINED",
                    remaining_freshness_days=1,
                    effective_freshness_limit_days=10,
                )
            ],
            "freshness_pressure_ratio": Decimal("0.30"),
        }
    )

    result = evaluate_procurement_business_signals(
        as_of=AS_OF, snapshot=snapshot, scenario_results=[]
    )

    assert INVENTORY_FRESHNESS_PRESSURE not in result["signals"]


def test_missing_freshness_policy_skips_with_warning(complete_logistics_snapshot):
    snapshot = complete_logistics_snapshot.model_copy(
        update={
            "on_hand_by_lot": [_lot(remaining_freshness_days=1, effective_freshness_limit_days=10)],
            "freshness_pressure_ratio": None,
        }
    )

    result = evaluate_procurement_business_signals(
        as_of=AS_OF, snapshot=snapshot, scenario_results=[]
    )

    assert INVENTORY_FRESHNESS_PRESSURE not in result["signals"]
    assert FRESHNESS_PRESSURE_POLICY_UNRESOLVED in result["warnings"]


# ---------------------------------------------------------------------------
# SCENARIO_ADJUSTMENT_REQUIRED — conditional ≥ 1 에서만
# ---------------------------------------------------------------------------


def test_conditional_scenario_raises_adjustment_signal(complete_logistics_snapshot):
    result = evaluate_procurement_business_signals(
        as_of=AS_OF,
        snapshot=complete_logistics_snapshot,
        scenario_results=[_scenario("conditional", [_quantity_adjustment()])],
    )

    assert SCENARIO_ADJUSTMENT_REQUIRED in result["signals"]


def test_ok_only_and_reject_only_do_not_raise_adjustment_signal(complete_logistics_snapshot):
    for verdict in ("ok", "reject"):
        result = evaluate_procurement_business_signals(
            as_of=AS_OF,
            snapshot=complete_logistics_snapshot,
            scenario_results=[_scenario(verdict, [])],
        )

        assert SCENARIO_ADJUSTMENT_REQUIRED not in result["signals"]


# ---------------------------------------------------------------------------
# preferred_adjustment 집계 — 고유 축 1종만 채택, 혼재·0건은 null
# ---------------------------------------------------------------------------


def test_preferred_is_the_single_axis():
    results = [
        _scenario("conditional", [_quantity_adjustment()]),
        _scenario("conditional", [_quantity_adjustment()]),
    ]

    assert derive_preferred_adjustment(results) == "quantity"


def test_mixed_axes_yield_no_preferred():
    results = [
        _scenario("conditional", [_quantity_adjustment()]),
        _scenario("conditional", [_timing_adjustment()]),
    ]

    assert derive_preferred_adjustment(results) is None


def test_no_adjustments_yield_no_preferred():
    assert derive_preferred_adjustment([_scenario("ok", [])]) is None


# ---------------------------------------------------------------------------
# Sales — 비율 Rule 전환 (status 의존 폐기)
# ---------------------------------------------------------------------------


def test_sales_freshness_risk_comes_from_ratio_not_status(complete_logistics_snapshot):
    """NEEDS_PRIORITY_SHIPMENT status 없이도 비율만으로 판매 위험이 선다."""
    snapshot = complete_logistics_snapshot.model_copy(
        update={
            "on_hand_by_lot": [
                _lot(status="ACTIVE", remaining_freshness_days=2, effective_freshness_limit_days=10)
            ],
            "freshness_pressure_ratio": Decimal("0.30"),
        }
    )

    result = evaluate_sales_business_signals(snapshot=snapshot)

    assert result["signals"] == [FRESHNESS_QUALITY_RISK]


# ---------------------------------------------------------------------------
# 어댑터 조립 — signal 은 soft_warnings 로 나가고 M-1 missing_data 에 섞이지 않는다
# ---------------------------------------------------------------------------


def _wire_snapshot(monkeypatch, snapshot) -> None:
    policy = LogisticsPolicy(
        guaranteed_capacity_kg=snapshot.guaranteed_capacity_kg,
        burst_capacity_kg=snapshot.burst_capacity_kg,
        inbound_lead_days=snapshot.inbound_lead_days,
        daily_inbound_capacity_kg=snapshot.daily_inbound_capacity_kg,
        inbound_transport_capacity_kg=snapshot.inbound_transport_capacity_kg,
        shared_daily_outbound_capacity_kg=snapshot.shared_daily_outbound_capacity_kg,
        cap_by_date_policy="CONFIRMED_ONLY",
        policy_version=POLICY_VERSION,
        usage_scope="AGENT_MVP_DEMO",
        source_refs={},
    )
    swap_in_modes(
        monkeypatch,
        "load_read",
        lambda *, as_of, sim_run_id: LogisticsRead(snapshot=snapshot, policy=policy),
    )


def _scenario_validation_request(payload) -> AgentRequest:
    return AgentRequest(
        context=ExecutionContext(
            request_id="REQ-SIGNAL-0001",
            as_of=AS_OF,
            trigger="USER_REQUEST",
            policy_version="POLICY-V1",
            sim_run_id="SIM-T-SIGNAL-0001",
        ),
        agent="inventory",
        mode="SCENARIO_VALIDATION",
        payload=payload,
    )


def test_scenario_validation_carries_signal_and_policy_warning_not_missing_data(
    monkeypatch, logistics_purchase_payload, complete_logistics_snapshot
):
    snapshot = complete_logistics_snapshot.model_copy(
        update={
            "on_hand_by_lot": [_lot(remaining_freshness_days=2, effective_freshness_limit_days=10)],
            "used_capacity_kg": Decimal(500),
            "freshness_pressure_ratio": Decimal("0.30"),
            # capacity_tight_ratio 미등록 → 판정 스킵 사실이 경고로 나가야 한다
            "capacity_tight_ratio": None,
        }
    )
    _wire_snapshot(monkeypatch, snapshot)

    reply, _meta = adapter.logistics_port(_scenario_validation_request(logistics_purchase_payload))

    assert reply.runtime_status == "READY"
    warnings = reply.payload["soft_warnings"]
    assert INVENTORY_FRESHNESS_PRESSURE in warnings
    assert CAPACITY_TIGHT_POLICY_UNRESOLVED in warnings
    # M-1 missing_data 는 마스터가 사용자에게 달라고 할 필드 이름이다 — 업무 위험 signal 도,
    # 맨 경고 코드도 섞이지 않는다.
    assert all(
        code not in name
        for name in reply.missing_data
        for code in (INVENTORY_FRESHNESS_PRESSURE, CAPACITY_TIGHT_POLICY_UNRESOLVED)
    )


def test_sales_signal_stays_off_when_lot_is_fresh(complete_logistics_snapshot):
    """잔여 9/10 Lot 은 판매 신선도 위험이 아니다 — 비율 임계 30% 위."""
    snapshot = complete_logistics_snapshot.model_copy(
        update={
            "on_hand_by_lot": [_lot(remaining_freshness_days=9, effective_freshness_limit_days=10)],
            "freshness_pressure_ratio": Decimal("0.30"),
        }
    )

    result = evaluate_sales_business_signals(snapshot=snapshot)

    assert FRESHNESS_QUALITY_RISK not in result["signals"]


# ---------------------------------------------------------------------------
# 신선도 Lot 분류 추출 (#396)
#
# ★ **signal 은 그대로여야 한다.** 만료 Lot 을 새로 세면서 판정 입력이 바뀌면
#   `INVENTORY_FRESHNESS_PRESSURE` · `FRESHNESS_QUALITY_RISK` 의 뜻이 조용히 달라진다.
#   그래서 wrapper 등가부터 고정하고, 그 위에 새 갈래를 잰다.
# ---------------------------------------------------------------------------


def _census_snapshot(complete_logistics_snapshot, lots, **overrides):
    return complete_logistics_snapshot.model_copy(update={"on_hand_by_lot": lots, **overrides})


def test_census_splits_active_lots_into_three_exclusive_buckets(complete_logistics_snapshot):
    """세 갈래는 배타이고 합이 ACTIVE Lot 수다 — 두 번 세이지도, 사라지지도 않는다."""
    snapshot = _census_snapshot(
        complete_logistics_snapshot,
        [
            _lot(lot_id="OK", remaining_freshness_days=8, effective_freshness_limit_days=10),
            _lot(lot_id="NONE", remaining_freshness_days=None, effective_freshness_limit_days=None),
            _lot(lot_id="GONE", remaining_freshness_days=-2, effective_freshness_limit_days=10),
            _lot(lot_id="ZERO", remaining_freshness_days=0, effective_freshness_limit_days=10),
            _lot(lot_id="LIMIT0", remaining_freshness_days=3, effective_freshness_limit_days=0),
            _lot(lot_id="HOLD", remaining_freshness_days=1, status="HOLD"),
        ],
    )

    census = collect_freshness_lot_census(snapshot)

    assert census.ratios == [Decimal("0.8")]
    assert census.expired_lot_count == 2  # GONE · ZERO — 0 도 만료다
    assert census.unresolved_lot_count == 2  # NONE · LIMIT0
    # ACTIVE 5건이 세 갈래로 남김없이 갈렸다. HOLD 는 모집단이 아니다.
    assert len(census.ratios) + census.expired_lot_count + census.unresolved_lot_count == 5


def test_census_counts_expired_before_limit_check(complete_logistics_snapshot):
    """🔴 갈래의 **순서가 계약이다** — 잔여 미확인을 먼저 보고 그 다음 만료다.

    순서가 뒤집히면 `remaining=None` 이 만료로 읽혀 *"모른다"* 가 *"상했다"* 가 된다.
    """
    snapshot = _census_snapshot(
        complete_logistics_snapshot,
        [_lot(remaining_freshness_days=None, effective_freshness_limit_days=None)],
    )

    census = collect_freshness_lot_census(snapshot)

    assert census.unresolved_lot_count == 1
    assert census.expired_lot_count == 0


def test_pressure_inputs_wrapper_is_the_census_projection(complete_logistics_snapshot):
    """★ 추출 전과 **글자 그대로 같은 반환**이다 — signal 입력이 안 바뀌었다는 근거다."""
    snapshot = _census_snapshot(
        complete_logistics_snapshot,
        [
            _lot(lot_id="OK", remaining_freshness_days=8, effective_freshness_limit_days=10),
            _lot(lot_id="NONE", remaining_freshness_days=None, effective_freshness_limit_days=None),
            _lot(lot_id="GONE", remaining_freshness_days=-2, effective_freshness_limit_days=10),
        ],
    )

    census = collect_freshness_lot_census(snapshot)
    ratios, unresolved = collect_freshness_pressure_inputs(snapshot)

    assert (ratios, unresolved) == (census.ratios, census.unresolved_lot_count)
    # 🔴 만료 Lot 은 **압박 입력에 섞이지 않는다.** 새로 세게 됐다고 판정 모집단이
    #    넓어지면 `INVENTORY_FRESHNESS_PRESSURE` 의 뜻이 달라진다.
    assert unresolved == 1
    assert ratios == [Decimal("0.8")]


def test_expired_lot_does_not_raise_a_freshness_signal(complete_logistics_snapshot):
    """★ 만료만 있는 날 signal 은 서지 않는다 — 그 규칙을 #396 이 바꾸지 않았다."""
    snapshot = _census_snapshot(
        complete_logistics_snapshot,
        [_lot(remaining_freshness_days=-5, effective_freshness_limit_days=10)],
        freshness_pressure_ratio=Decimal("0.30"),
    )

    procurement = evaluate_procurement_business_signals(
        as_of=AS_OF, snapshot=snapshot, scenario_results=[]
    )
    sales = evaluate_sales_business_signals(snapshot=snapshot)

    assert INVENTORY_FRESHNESS_PRESSURE not in procurement["signals"]
    assert FRESHNESS_QUALITY_RISK not in sales["signals"]
    # 만료는 미확인이 아니다 — 경고도 붙지 않는다
    assert LOT_FRESHNESS_UNRESOLVED not in procurement["warnings"]


# ---------------------------------------------------------------------------
# measure_freshness_facts — signal 과 분리된 순수 측정 (#396)
# ---------------------------------------------------------------------------


def test_measure_facts_needs_no_signal_to_report(complete_logistics_snapshot):
    """🔴 **이 함수의 존재 이유다.** signal 이 안 서도 측정은 나온다.

    종전에는 `_record_freshness_measurements` 가 signal 발화 시에만 불려, 위험이
    없는 날에는 최소 비율조차 어디에도 없었다.
    """
    snapshot = _census_snapshot(
        complete_logistics_snapshot,
        [_lot(remaining_freshness_days=9, effective_freshness_limit_days=10)],
        freshness_pressure_ratio=Decimal("0.30"),
    )

    signals = evaluate_procurement_business_signals(
        as_of=AS_OF, snapshot=snapshot, scenario_results=[]
    )
    facts = measure_freshness_facts(snapshot=snapshot)

    assert INVENTORY_FRESHNESS_PRESSURE not in signals["signals"]  # 위험 없음
    assert signals["measurements"] == {}  # 판정 수치도 없다
    assert facts["freshness_min_remaining_ratio"] == Decimal("0.9")  # 그래도 잰다
    assert facts["freshness_risk_lot_count"] == 0
    assert facts["freshness_unresolved_lot_count"] == 0
    assert facts["freshness_expired_lot_count"] == 0


def test_measure_facts_and_signal_count_the_same_risk_lots(complete_logistics_snapshot):
    """★ 비교식이 한 곳이라 두 수가 갈릴 수 없다 (`count_freshness_risk_lots`).

    두 벌이면 *"signal 은 섰는데 위험 Lot 0건"* 인 회신이 나올 수 있다.
    """
    snapshot = _census_snapshot(
        complete_logistics_snapshot,
        [
            _lot(lot_id="R1", remaining_freshness_days=3, effective_freshness_limit_days=10),
            _lot(lot_id="R2", remaining_freshness_days=2, effective_freshness_limit_days=10),
            _lot(lot_id="OK", remaining_freshness_days=9, effective_freshness_limit_days=10),
        ],
        freshness_pressure_ratio=Decimal("0.30"),
    )

    signals = evaluate_procurement_business_signals(
        as_of=AS_OF, snapshot=snapshot, scenario_results=[]
    )
    facts = measure_freshness_facts(snapshot=snapshot)

    assert INVENTORY_FRESHNESS_PRESSURE in signals["signals"]
    assert signals["measurements"]["freshness_risk_lot_count"] == 2
    assert facts["freshness_risk_lot_count"] == 2
    assert (
        facts["freshness_min_remaining_ratio"]
        == signals["measurements"]["freshness_min_remaining_ratio"]
    )


def test_measure_facts_omits_risk_count_without_a_threshold(complete_logistics_snapshot):
    """🔴 기준 없는 `0` 은 *"확인했고 없음"* 으로 읽힌다 — 키를 아예 안 만든다."""
    snapshot = _census_snapshot(
        complete_logistics_snapshot,
        [_lot(remaining_freshness_days=1, effective_freshness_limit_days=10)],
        freshness_pressure_ratio=None,
    )

    facts = measure_freshness_facts(snapshot=snapshot)

    assert "freshness_risk_lot_count" not in facts
    # 임계와 무관한 측정은 그대로 나온다
    assert facts["freshness_min_remaining_ratio"] == Decimal("0.1")
    assert facts["freshness_unresolved_lot_count"] == 0
    assert facts["freshness_expired_lot_count"] == 0


def test_measure_facts_omits_min_ratio_when_nothing_is_computable(complete_logistics_snapshot):
    """비율을 셈할 Lot 이 없으면 최솟값은 **없는 것**이다 — 0 으로 덮지 않는다."""
    snapshot = _census_snapshot(
        complete_logistics_snapshot,
        [
            _lot(lot_id="NONE", remaining_freshness_days=None, effective_freshness_limit_days=None),
            _lot(lot_id="GONE", remaining_freshness_days=-1, effective_freshness_limit_days=10),
        ],
        freshness_pressure_ratio=Decimal("0.30"),
    )

    facts = measure_freshness_facts(snapshot=snapshot)

    assert "freshness_min_remaining_ratio" not in facts
    assert facts["freshness_risk_lot_count"] == 0  # 임계는 있고 셈할 Lot 이 없다
    assert facts["freshness_unresolved_lot_count"] == 1
    assert facts["freshness_expired_lot_count"] == 1


def test_risk_count_boundary_is_inclusive(complete_logistics_snapshot):
    """★ 경계는 `<=` 다 — signal 을 세우는 `any(ratio <= threshold)` 와 같아야 한다.

    한쪽만 `<` 로 두면 *"signal 은 섰는데 위험 Lot 0건"* 이 성립한다.
    """
    snapshot = _census_snapshot(
        complete_logistics_snapshot,
        [_lot(remaining_freshness_days=3, effective_freshness_limit_days=10)],
        freshness_pressure_ratio=Decimal("0.30"),  # 3/10 = 정확히 0.30
    )

    signals = evaluate_procurement_business_signals(
        as_of=AS_OF, snapshot=snapshot, scenario_results=[]
    )

    assert INVENTORY_FRESHNESS_PRESSURE in signals["signals"]
    assert measure_freshness_facts(snapshot=snapshot)["freshness_risk_lot_count"] == 1
