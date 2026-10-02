"""마스터 회신에 싣는 근거(Evidence) · 참조 · 숫자 표기 — 계산하지 않고 옮긴다.

근거 도우미와 밴드 상수를 모아 둔다. 네 mode 의 service
(`agent_status` · `pre_purchase` · `pre_sales` · `scenario_validation`)가 같이 쓴다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from app.contracts.core import Evidence, Verdict
from app.logistics.domain.tools import (
    CAP_BY_DATE_WINDOW_DAYS,
    build_cap_window,
    calculate_cap_by_date,
)
from app.logistics.schemas.snapshot import InventoryLogisticsSnapshot, LogisticsPolicy
from app.purchase_agent.schemas.proposal import PurchaseProposal

JUDGMENT_FIELDS = ("cap_by_date_policy",)

CAP_WINDOW_DAYS = CAP_BY_DATE_WINDOW_DAYS
"""PRE_PURCHASE 에서 `cap_by_date` 를 뽑는 조회 창의 길이 — 물류 Tool 소유값.

제약값이 아니다. 물류의 `calculate_cap_by_date()` 는 도착일 목록을 받는데,
제안 전에는 도착일이 없다. 그래서 `as_of + lead` 부터 이 길이만큼 훑는다.

  짧으면 매입이 덜 볼 뿐 값이 달라지지 않는다. 18 인 것은 매입 커버일수 상한
  D+18(ML 지평)에서 왔다 — 그보다 뒤의 날짜는 매입이 쓰지 않는다.

  창의 길이 자체는 `cap_by_date_window_days` 로 payload 에 밝힌다. 받는 쪽이
  "이 날짜까지밖에 안 왔다" 를 알아야 없는 날을 0 으로 읽지 않는다.

값은 `tools.CAP_BY_DATE_WINDOW_DAYS` 를 그대로 쓴다 (#121 ⑤) — 여기서 따로 숫자를
들고 있으면 판정 창과 조회 창이 갈린다. 이름을 여기 두는 것은 Evidence
문구·payload 키가 이 이름을 참조하기 때문이다.
"""

RULE_PREFIX = "logistics_rule/"
"""물류 규칙이 낸 `ConstraintCode` 에 붙이는 접두어 — 출처를 이름에 남긴다."""

RENTAL_CAP_KEY = "rental_cap_kg"
RENTAL_CAP_KG = 0.0
RENTAL_CAP_REF = "LOGISTICS-REPLY-20260827:rental_cap_kg"
"""외부 창고 임차 상한. 1차 MVP 는 임차 기능이 없다 (2026-08-27 물류 회신 §1 · §6).

`0` 은 "모른다" 가 아니라 "임차 가능량이 0 으로 확정됐다" 다. 매입은 이 값을
창고 상한에 더하므로 둘의 구분이 결과를 바꾼다 — 모르는 값을 0 으로 쓰면 살 수
있는 양을 실제보다 적게 잡고, 확정 0 을 미확정으로 두면 매입이 아예 못 돈다.

상수로 둔 것은 DB 에 키가 없어서다. `missing_data` 에 출처 부재를 남긴다.
"""

VERDICT_MAP: Mapping[str, Verdict] = {
    "PASS": "ok",
    "REVIEW_REQUIRED": "conditional",
    "FAIL": "reject",
}
"""물류 `FinalVerdict` → 공통 `Verdict`.

`REVIEW_REQUIRED` 를 `conditional` 로 옮기는 것은 재무와 같은 매핑이다 (정의서 §7.1).
"""


def free_capacity(snapshot: InventoryLogisticsSnapshot) -> Decimal | None:
    """보장 capacity − 현재 점유. 물류 `calculate_cap_by_date` 의 정의를 따른다."""
    if snapshot.guaranteed_capacity_kg is None:
        return None
    return max(Decimal(0), snapshot.guaranteed_capacity_kg - snapshot.used_capacity_kg)


def cap_window(snapshot: InventoryLogisticsSnapshot, as_of: date) -> dict[date, Decimal] | None:
    """제안 전이라 도착일이 없다 — 물류 Tool 의 조회 창을 그대로 훑는다.

    창의 정의(시작일·길이)는 `tools.build_cap_window` 소유다 (#121 ⑤). 여기서 같은 날짜
    나열을 따로 만들면 판정 창과 조회 창이 갈릴 자리가 생긴다.
    """
    dates = build_cap_window(snapshot, as_of)
    if dates is None:
        return None
    try:
        return calculate_cap_by_date(snapshot, dates)
    except ValueError:
        # IN_TRANSIT_SCHEDULE_UNRESOLVED · LOGISTICS_CAPACITY_INPUT_MISSING ·
        # NEGATIVE_PROJECTED_OCCUPANCY — 전부 "지금은 못 낸다" 다
        return None


def as_proposal(payload: Mapping[str, Any]) -> PurchaseProposal | None:
    """봉투 payload → `PurchaseProposal`.

    `allowed_axes` 처럼 제안 모델에 없는 키는 걸러 낸다 — 모델이 `extra="forbid"` 라
    그대로 넣으면 통째로 실패한다. 모르는 키를 버리는 것이지 값을 고치지 않는다.
    """
    known = {key: payload[key] for key in PurchaseProposal.model_fields if key in payload}
    try:
        return PurchaseProposal.model_validate(known)
    except Exception:  # noqa: BLE001 — 못 읽는 것은 예외가 아니라 상태다
        return None


def snapshot_ref(snapshot: InventoryLogisticsSnapshot) -> str:
    refs = snapshot.evidence_refs
    return refs[0] if refs else "logistics:snapshot"


def lots_ref_of(snapshot: InventoryLogisticsSnapshot) -> str:
    """Lot 근거는 Lot 을 실제로 담은 참조를 가리킨다.

    스냅샷 첫 참조를 쓰면 Lot 근거가 runtime fixture 를 가리키게 되는데, 나중에
    "이 수량이 어디서 왔나" 를 따라가면 엉뚱한 곳에 닿는다.
    """
    for candidate in snapshot.evidence_refs:
        if "inventory_lots" in candidate:
            return candidate
    return snapshot_ref(snapshot)


def policies_ref_of(snapshot: InventoryLogisticsSnapshot) -> str:
    """품목 보관 정책 근거는 정책 테이블을 담은 참조를 가리킨다 (`lots_ref_of` 와 같은 이유)."""
    for candidate in snapshot.evidence_refs:
        if "item_storage_policies" in candidate:
            return candidate
    return snapshot_ref(snapshot)


def policy_ref(policy: LogisticsPolicy, key: str, fallback: str) -> str:
    return policy.source_refs.get(key, fallback)


def to_float(value: Decimal | float) -> float:
    return float(value)


def to_float_or_none(value: Decimal | float | None) -> float | None:
    """`None` 은 `None` 으로 둔다. 0 으로 바꾸지 않는다 (§1.2-10).

    `to_float` 과 나눠 두는 이유는 «있어야 하는 값» 과 «없을 수 있는 값» 을 호출부에서
    구분하기 위해서다 — `to_float(None)` 이 `TypeError` 로 죽는 것이 옳고, null 이
    정상인 자리만 이 함수를 쓴다.
    """
    return None if value is None else float(value)


def evidence(
    claim: str,
    value: Any,
    unit: str,
    ref: str,
    detail: str = "",
    grade: str = "OFFICIAL",
    source: str = "inventory",
    extra_ref_ids: tuple[str, ...] = (),
) -> Evidence:
    return Evidence(
        claim=claim,
        source=source,  # type: ignore[arg-type]
        ref_ids=(ref, *extra_ref_ids),
        value=float(value),
        unit=unit,
        evidence_grade=grade,  # type: ignore[arg-type]
        evidence_detail=detail,
    )


def inventory_by_item_evidences(
    rows: list[dict[str, Any]],
    snapshot: InventoryLogisticsSnapshot,
    *,
    prefix: str = "",
) -> list[Evidence]:
    """품목별 가용재고 근거 — 배열 항목 안의 숫자마다, 이름 선택자로 (#111 A1).

    ref 는 집계 원본을 가리킨다. `snapshot_ref()`(스냅샷 첫 참조)를 쓰면 runtime fixture
    에 닿는데, 이 kg 은 Lot 행 합산 − 확정 출고 차감이다 — `lots_ref_of` docstring 이
    금지한 바로 그 경우다. 확정 출고 출처(스냅샷 첫 참조)는 보조 ref 로 함께 싣는다.
    """
    lots_ref = lots_ref_of(snapshot)
    outbound_ref = snapshot_ref(snapshot)
    return [
        evidence(
            f"{prefix}inventory_by_item[{row['item']}].available_qty_kg",
            row["available_qty_kg"],
            "kg",
            lots_ref,
            f"{row['item']} 가용재고 합계 — 비-ACTIVE·신선도 만료 Lot 제외, 확정 출고 예약분 차감",
            source="tool_calc",
            extra_ref_ids=(outbound_ref,) if outbound_ref != lots_ref else (),
        )
        for row in rows
    ]
