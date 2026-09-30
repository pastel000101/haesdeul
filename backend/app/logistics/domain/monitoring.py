"""Detect — 관측을 **지속되는 문제**로 바꾼다. 전부 결정론이다.

```text
① observe                     관측 한 벌 (읽기만)
② 탐지기를 돈다               순수 함수: (관측) → 지금 참인 조건들
③ dedupe 축으로 맞댄다        (sim_run_id, code, subject_type, subject_id)
     없으면  INSERT  status=OPEN · opened_as_of=as_of
     있으면  UPDATE  evidence · severity · last_detected_as_of   🔴 status 는 안 건드린다
④ 이번에 안 잡힌 살아 있는 행  RESOLVED  (AFTER_OUTBOUND 에서만)
```

🔴 **임계 비교를 두 벌 적지 않는다.** 신선도 경계는 `rules.count_freshness_risk_lots`
   (`ratio <= threshold`), 용량 경계는 `rules.evaluate_procurement_business_signals` 가
   `CAPACITY_TIGHT` 를 세우는 그 비교(`usage >= tight_ratio`)다. 같은 창고 상태를 두고
   회신은 *"위험 신호 있음"* 인데 Exception 은 0 건인 날이 오면 안 된다.

🔴 **LLM 이 없다.** Commit 2 는 탐지·해소까지다 — 걷기가 매일 돌기 때문에, 여기에
   모델 호출이 들어가면 `--reset` 뒤 같은 걷기가 다른 결과를 낸다 (상세설계 §17).

⚠️ **기준이 없으면 «문제 없음» 이 아니라 «안 쟀다» 다.** 임계 정책이 없거나 사용률을
   못 셈한 날, 그 탐지기는 조건을 내지도 않고 **기존 행을 닫지도 않는다** — 닫으면
   정책 미등재가 *"해결됐다"* 로 장부에 남는다.

★ 2026-09-30 재구성 BL-015: `logistics/monitoring/detect.py` 에서 탐지기(순수)와
  임계 상수가 이 파일로 왔다. 장부 반영 순서(`detect_logistics_exceptions`)는
  `service/monitoring.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from app.logistics.domain.rules import (
    CAPACITY_TIGHT_POLICY_UNRESOLVED,
    FRESHNESS_PRESSURE_POLICY_UNRESOLVED,
    count_freshness_risk_lots,
)
from app.logistics.schemas.monitoring import (
    CAPACITY_PRESSURE,
    CAPACITY_WINDOW_USAGE_UNRESOLVED,
    FRESHNESS_PRESSURE,
    POLICY_OBSERVED_AS_OF,
    WAREHOUSE_SUBJECT_ID,
    DetectedCondition,
    ExceptionCode,
    ExceptionEvidence,
    ObservedLot,
    Severity,
    WarehouseObservation,
    derive_observed_as_of,
)
from app.logistics.schemas.vocabulary import ACTIVE_LOT_STATUS

#: 🟡 **Simulation Assumption 이다.** 정책 표에 이 경계를 담은 칸이 없다 — 지어내서
#: `agent_policy_config` 에 넣지 않고 탐지기 상수로 둔다. 정책으로 승격하는 날
#: `detector_version` 이 오른다 (상세설계 §26).
FRESHNESS_CRITICAL_REMAINING_DAYS = 1
#: 🟡 **Simulation Assumption.** 0.90(정책값) 이상이 MEDIUM, 여기부터 HIGH.
CAPACITY_HIGH_RATIO = Decimal("0.95")
#: 🟡 **Simulation Assumption.** 보장 용량을 다 쓴 상태.
#: ⚠️ `calculate_window_capacity_usage` 는 점유가 보장치를 넘으면 사용률을 1 에서
#:    멈춘다(cap 이 0 으로 클램프된다) — 그래서 이 구간은 «정확히 1» 이다.
CAPACITY_CRITICAL_RATIO = Decimal("1.00")

FRESHNESS_DETECTOR_VERSION = "v1"
CAPACITY_DETECTOR_VERSION = "v1"

#: 무엇이 닫았나 (`logistics_exceptions.resolved_by`).
#:
#: ⚠️ **원장 이동 ID(`MOVE-…` · `ALC-…`)를 적지 않는다.** Commit 2 는 Lot 별 이동을
#:    읽지 않고 관측 결과만 본다 — 없는 ID 를 지어내는 대신 **무엇이 닫았는지의 갈래**를
#:    적는다. 이동 ID 까지 적는 것은 전/후 비교를 만드는 Commit 6 의 일이다.
REDETECT = "REDETECT"
LOT_EMPTY = "LOT_EMPTY"
COMMITTED = "COMMITTED"
ESCALATED_FRESHNESS_EXPIRED = "ESCALATED:FRESHNESS_EXPIRED"


@dataclass(frozen=True)
class DetectorOutcome:
    """탐지기 하나가 이번에 한 일. 🔴 **`ran` 이 거짓이면 그 코드는 닫지도 않는다.**

    ```text
    ran=True   재 봤다 — 여기 없는 살아 있는 행은 «조건이 사라진 것»
    ran=False  못 쟀다 — 기준·입력이 없었다. 기존 행을 건드리지 않는다
    ```
    """

    code: ExceptionCode
    ran: bool
    conditions: tuple[DetectedCondition, ...] = ()
    skipped: str = ""


# ---------------------------------------------------------------------------
# ① FRESHNESS_PRESSURE — Core 메인
# ---------------------------------------------------------------------------


def detect_freshness_pressure(observation: WarehouseObservation) -> DetectorOutcome:
    """아직 팔 수 있는데 **시간이 얼마 안 남은** Lot. Lot 단위다.

    ```text
    status == 'ACTIVE'                              팔 수 있는 재고만
    remaining_freshness_days > 0                    🔴 만료는 이 문제가 아니다 (§7.1 E)
    remaining / effective_limit <= 0.30             기존 신호와 **같은 비교**
    uncommitted_kg > 0                              이미 팔린 Lot 을 또 팔라고 하지 않는다
    ```

    🔴 **`uncommitted_kg` 조건이 이 탐지기의 핵심이다.** 없으면 이미 판매 확정·할당된
       Lot 까지 *"빨리 파세요"* 로 올라오고, 사람이 매일 같은 제안을 지우게 된다.
       ⚠️ 그 값이 `None` 인 Lot 은 **셈을 못 한 것**이라 조건을 세우지 않는다 —
       예약 축을 못 읽었거나(관측의 `uncertainties`), 애초에 판매 가용이 아닌 Lot 이다.

    ★ **비율의 분모는 유효 한계다** (`중` 등급 계수 반영). 원값을 쓰면 갓 입고된
      중 등급이 즉시 임박으로 잡힌다 — `tools.collect_freshness_lot_census` 가 같은
      이유로 같은 분모를 쓴다.
    """
    threshold = observation.policy.freshness_pressure_ratio
    if threshold is None:
        return DetectorOutcome(
            code=FRESHNESS_PRESSURE, ran=False, skipped=FRESHNESS_PRESSURE_POLICY_UNRESOLVED
        )

    conditions: list[DetectedCondition] = []
    for lot in observation.lots:
        if lot.status != ACTIVE_LOT_STATUS:
            continue
        remaining = lot.remaining_freshness_days
        if remaining is None or remaining <= 0:
            continue
        ratio = lot.freshness_remaining_ratio
        if ratio is None:
            continue
        # 🔴 **경계(`<=`)의 주인은 `count_freshness_risk_lots` 하나다.** 여기서
        #    `비율 <= 임계` 를 다시 적으면, 그 함수의 경계가 바뀌는 날 *"위험 Lot 3건"*
        #    이라고 답한 회신과 Exception 이 서로 다른 수를 세게 된다.
        if count_freshness_risk_lots([ratio], threshold) == 0:
            continue
        uncommitted = lot.uncommitted_kg
        if uncommitted is None or uncommitted <= 0:
            continue
        conditions.append(
            _freshness_condition(lot, ratio=ratio, threshold=threshold, remaining=remaining)
        )
    return DetectorOutcome(code=FRESHNESS_PRESSURE, ran=True, conditions=tuple(conditions))


def _freshness_severity(*, remaining: int, sell_priority_remaining_days: int | None) -> Severity:
    """얼마나 급한가. 🟡 **구간이 Simulation Assumption 이다.**

    ```text
    remaining <= 1                              CRITICAL  내일이면 못 판다
    remaining <= sell_priority_remaining_days   HIGH      회전 정책이 «우선 팔라» 고 한 구간
    그 밖의 압박 구간                            MEDIUM
    ```

    ★ **HIGH 의 경계만 정책 표에서 온다** (`item_turnover_policies` · 배추 3 · 무 4 ·
      양파 7). 🔴 정책이 없는 품목(실측 5 중 2)은 그 경계를 **지어내지 않고** MEDIUM 에
      머문다 — 모르는 것을 더 급하다고도 덜 급하다고도 말하지 않는다.
    """
    if remaining <= FRESHNESS_CRITICAL_REMAINING_DAYS:
        return "CRITICAL"
    if sell_priority_remaining_days is not None and remaining <= sell_priority_remaining_days:
        return "HIGH"
    return "MEDIUM"


def _freshness_condition(
    lot: ObservedLot, *, ratio: Decimal, threshold: Decimal, remaining: int
) -> DetectedCondition:
    """근거 한 줄마다 **그 사실의** 관측일을 붙인다 (§18).

    🔴 **Lot 하나에 관측일 하나를 돌려쓰지 않는다.** 입고일은 `received_at` 을 재는
       날이지 잔량·상태·미확정 물량을 재는 날이 아니다 — 한 날짜로 뭉개면 D8 까지
       나간 재고가 *"D1 부터 알던 사실"* 로 장부에 남는다.

    ```text
    remaining_freshness_days        입고일 + 보관정책   → 정책에 유효일이 없어 None
    effective_freshness_limit_days  보관정책            → None
    freshness_remaining_ratio       위 둘               → None
    freshness_pressure_ratio        정책                → None
    remaining_qty_kg                원장 마지막 이동일   ✅ 진짜 날짜가 붙는 유일한 칸
    uncommitted_kg                  잔량 + 예약·할당 축 → 예약 축을 못 대서 None
    sell_priority_remaining_days    회전정책            → None
    ```
    """
    limit = lot.effective_freshness_limit_days
    assert limit is not None  # 비율이 섰다는 것이 곧 한계가 있다는 뜻이다
    evidence = [
        ExceptionEvidence(
            fact="remaining_freshness_days",
            value=Decimal(remaining),
            unit="일",
            source="inventory_lots+item_storage_policies",
            source_id=lot.lot_id,
            # 🔴 **입고일이 아니다.** «한계 − 경과» 이고 그 한계가 정책에서 온다 —
            #    `source` 가 두 표를 적는 그대로 관측일도 둘의 늦은 쪽이다.
            observed_as_of=lot.freshness_observed_as_of,
        ),
        ExceptionEvidence(
            fact="effective_freshness_limit_days",
            value=Decimal(limit),
            unit="일",
            source="item_storage_policies",
            source_id=lot.item,
            observed_as_of=POLICY_OBSERVED_AS_OF,
        ),
        ExceptionEvidence(
            fact="freshness_remaining_ratio",
            value=ratio,
            unit="비율",
            source="tool_calc:collect_freshness_lot_census",
            source_id=lot.lot_id,
            # 잔여 ÷ 유효 한계 — 분자·분모가 모두 정책을 지난다.
            observed_as_of=lot.freshness_observed_as_of,
        ),
        ExceptionEvidence(
            fact="freshness_pressure_ratio",
            value=threshold,
            unit="비율",
            source="agent_policy_config",
            source_id="freshness_pressure_ratio",
            observed_as_of=POLICY_OBSERVED_AS_OF,
        ),
        ExceptionEvidence(
            fact="remaining_qty_kg",
            value=lot.remaining_qty_kg,
            unit="kg",
            source="inventory_lots",
            source_id=lot.lot_id,
            # ✅ 원장의 **마지막 이동일**. 이 Commit 에서 진짜 날짜가 붙는 유일한 칸이다.
            observed_as_of=lot.remaining_qty_observed_as_of,
        ),
        ExceptionEvidence(
            fact="uncommitted_kg",
            value=lot.uncommitted_kg if lot.uncommitted_kg is not None else Decimal(0),
            unit="kg",
            source="tool_calc:_sellable_lot_contributions",
            source_id=lot.lot_id,
            # 잔량 축과 예약·할당 축의 늦은 쪽. 뒤엣것을 못 대서 지금은 `None` 이다
            #  (`COMMITMENT_OBSERVED_AS_OF` — `cancel_allocation` 이 날짜를 안 남긴다).
            observed_as_of=lot.uncommitted_observed_as_of,
        ),
    ]
    if lot.sell_priority_remaining_days is not None:
        evidence.append(
            ExceptionEvidence(
                fact="sell_priority_remaining_days",
                value=Decimal(lot.sell_priority_remaining_days),
                unit="일",
                source="item_turnover_policies",
                source_id=lot.item_id or lot.item,
                observed_as_of=POLICY_OBSERVED_AS_OF,
            )
        )
    return DetectedCondition(
        code=FRESHNESS_PRESSURE,
        subject_type="LOT",
        subject_id=lot.lot_id,
        severity=_freshness_severity(
            remaining=remaining, sell_priority_remaining_days=lot.sell_priority_remaining_days
        ),
        detector_version=FRESHNESS_DETECTOR_VERSION,
        evidence=tuple(evidence),
        observed_as_of=derive_observed_as_of([one.observed_as_of for one in evidence]),
        note=f"{lot.item} {lot.lot_id} 잔여 {remaining}일 · 미확정 {lot.uncommitted_kg}kg",
    )


# ---------------------------------------------------------------------------
# ② CAPACITY_PRESSURE — Core 보조
# ---------------------------------------------------------------------------


def detect_capacity_pressure(observation: WarehouseObservation) -> DetectorOutcome:
    """창고가 빡빡하다. **창고 단위 하나**다.

    ```text
    calculate_window_capacity_usage >= capacity_tight_ratio
        기존 CAPACITY_TIGHT 와 **같은 함수 · 같은 비교**
    ```

    🔴 **새 용량 계산기를 만들지 않는다.** 창 구성(`as_of + lead` 부터 18일)도 사용률
       (`1 − min(cap)/guaranteed`)도 `tools` 소유이고, 이 탐지기는 **그 값을 받아
       임계와 견주기만** 한다.
    """
    usage_ratio = observation.capacity.window_usage_ratio
    threshold = observation.policy.capacity_tight_ratio
    if threshold is None:
        return DetectorOutcome(
            code=CAPACITY_PRESSURE, ran=False, skipped=CAPACITY_TIGHT_POLICY_UNRESOLVED
        )
    if usage_ratio is None:
        return DetectorOutcome(
            code=CAPACITY_PRESSURE, ran=False, skipped=CAPACITY_WINDOW_USAGE_UNRESOLVED
        )
    if usage_ratio < threshold:
        return DetectorOutcome(code=CAPACITY_PRESSURE, ran=True)

    evidence = [
        ExceptionEvidence(
            fact="capacity_window_usage_ratio",
            value=usage_ratio,
            unit="비율",
            source="tool_calc:calculate_window_capacity_usage",
            source_id=observation.sim_run_id,
            # 창 사용률은 **재고 축과 정책 축이 함께** 만든 값이다 (보장 용량 ·
            # 리드타임이 창을 세운다) — 정책에 유효일이 없어 지금은 `None` 이다.
            observed_as_of=derive_observed_as_of(
                [observation.inventory_observed_as_of, POLICY_OBSERVED_AS_OF]
            ),
        ),
        ExceptionEvidence(
            fact="capacity_tight_ratio",
            value=threshold,
            unit="비율",
            source="agent_policy_config",
            source_id="capacity_tight_ratio",
            observed_as_of=POLICY_OBSERVED_AS_OF,
        ),
        ExceptionEvidence(
            fact="used_capacity_kg",
            value=observation.capacity.used_kg,
            unit="kg",
            source="inventory_lots",
            source_id=observation.sim_run_id,
            # ✅ 물리 점유는 **그 Lot 들의 잔량 합**이라(`repository`) 잔량 축의
            #    관측일을 그대로 따라간다 — 한 Lot 이라도 못 대면 전체가 `None` 이다.
            #    🔴 입고일 최댓값이 **아니다**: 입고 뒤 출고·폐기가 점유를 바꾼다.
            observed_as_of=observation.inventory_observed_as_of,
        ),
    ]
    if observation.capacity.guaranteed_kg is not None:
        evidence.append(
            ExceptionEvidence(
                fact="guaranteed_capacity_kg",
                value=observation.capacity.guaranteed_kg,
                unit="kg",
                source="agent_policy_config",
                source_id="guaranteed_capacity_kg",
                observed_as_of=POLICY_OBSERVED_AS_OF,
            )
        )
    condition = DetectedCondition(
        code=CAPACITY_PRESSURE,
        subject_type="WAREHOUSE",
        subject_id=WAREHOUSE_SUBJECT_ID,
        severity=_capacity_severity(usage_ratio),
        detector_version=CAPACITY_DETECTOR_VERSION,
        evidence=tuple(evidence),
        observed_as_of=derive_observed_as_of([one.observed_as_of for one in evidence]),
        note=f"창 사용률 {usage_ratio} (임계 {threshold})",
    )
    return DetectorOutcome(code=CAPACITY_PRESSURE, ran=True, conditions=(condition,))


def _capacity_severity(usage: Decimal) -> Severity:
    """🟡 **구간이 Simulation Assumption 이다** — 정책 표에는 임계 하나(0.90)뿐이다."""
    if usage >= CAPACITY_CRITICAL_RATIO:
        return "CRITICAL"
    if usage >= CAPACITY_HIGH_RATIO:
        return "HIGH"
    return "MEDIUM"


#: 도는 순서. 🔴 **메인이 먼저다** — 요약 한 줄을 읽는 사람이 먼저 볼 것이 신선도다.
DETECTORS: tuple[Callable[[WarehouseObservation], DetectorOutcome], ...] = (
    detect_freshness_pressure,
    detect_capacity_pressure,
)
