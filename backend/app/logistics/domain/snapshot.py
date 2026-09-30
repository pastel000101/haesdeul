"""현재 시점 스냅샷의 규칙 — 정책 행 검증 · fixture 한 건 가르기 · 행 검사 · Lot 신선도 · 단가.

★ 2026-09-30 재구성 BL-015: `logistics/repository.py` 에서 판단 · 조립 부분을 옮겼다. SQL 은
  `repository/current.py`,
  연결과 읽는 순서는 `readmodel/current.py`.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from app.logistics.domain.grade import normalize_grade
from app.logistics.schemas.snapshot import (
    LOGISTICS_POLICY_VERSION,
    UNRESOLVED_SOURCE,
    InTransitItem,
    InventoryLotSnapshot,
    LogisticsPolicy,
    LogisticsRuntimeFixture,
    ScheduledQuantity,
)
from app.logistics.schemas.vocabulary import USAGE_SCOPE

_NUMERIC_POLICY_KEYS = {
    "guaranteed_capacity_kg",
    "burst_capacity_kg",
    "inbound_lead_days",
    "daily_inbound_capacity_kg",
    "inbound_transport_capacity_kg",
    "shared_daily_outbound_capacity_kg",
}
_TEXT_POLICY_KEYS = {"cap_by_date_policy"}
_REQUIRED_POLICY_KEYS = _NUMERIC_POLICY_KEYS | _TEXT_POLICY_KEYS
#: 선택 정책 2종 (LLM 정책 결정서 §4) — 업무 위험 signal 의 임계값.
#: _REQUIRED_POLICY_KEYS 로 승격 금지: DB 행이 없는 순간 스냅샷 전체가 실패해
#: 물류가 통째로 RUNTIME_NOT_READY 가 된다. 없으면 None → 해당 판정만 SKIPPED.
_OPTIONAL_NUMERIC_POLICY_KEYS = {
    "capacity_tight_ratio",
    "freshness_pressure_ratio",
    # 🔴 **납기 준비일 (WP-4 M4).** 여기 둔 것은 «없어도 된다» 가 아니라 «없으면 그
    #    값을 쓰는 판정만 멈춘다» 는 뜻이다 — `LogisticsPolicy.outbound_prep_lead_days`
    #    주석이 그 fail-closed 를 어디서 거는지 적고 있다. 필수로 올리면 납기와 무관한
    #    입고·Capacity 경로까지 통째로 멈춘다.
    "outbound_prep_lead_days",
}

#: 🔴 **정수여야 하는 NUMERIC 정책.** DB 는 `NUMERIC` 이라 `1.5` 도 담기는데, 날 수는
#:   반쪽이 없다. `inbound_lead_days` 가 원래 혼자 하던 검사를 이름 있는 집합으로
#:   옮겼다 — 두 번째 날짜 정책(`outbound_prep_lead_days`)이 생겼기 때문이다.
_INTEGER_POLICY_KEYS = {"inbound_lead_days", "outbound_prep_lead_days"}


def build_logistics_policy(rows: list[dict[str, object]]) -> LogisticsPolicy:
    values: dict[str, object] = {}
    source_refs: dict[str, str] = {}
    for row in rows:
        key = row.get("policy_key")
        if key not in _REQUIRED_POLICY_KEYS and key not in _OPTIONAL_NUMERIC_POLICY_KEYS:
            continue
        if key in values:
            raise ValueError(f"Duplicate Logistics policy key: {key}")
        if row.get("policy_version") != LOGISTICS_POLICY_VERSION:
            raise ValueError(f"Logistics policy_version mismatch: {key}")
        if row.get("usage_scope") != USAGE_SCOPE:
            raise ValueError(f"Logistics policy usage_scope mismatch: {key}")

        kind = row.get("value_kind")
        expected_kind = "TEXT" if key in _TEXT_POLICY_KEYS else "NUMERIC"
        if kind != expected_kind:
            raise ValueError(f"Invalid value_kind for Logistics policy {key}: {kind}")
        selected_column = "value_numeric" if kind == "NUMERIC" else "value_text"
        unused_columns = {"value_numeric", "value_text", "value_json"} - {selected_column}
        value = row.get(selected_column)
        if value is None or any(row.get(column) is not None for column in unused_columns):
            raise ValueError(f"Inconsistent value columns for Logistics policy: {key}")
        if kind == "NUMERIC" and (isinstance(value, bool) or not isinstance(value, Decimal)):
            raise TypeError(f"Invalid Python NUMERIC value for Logistics policy: {key}")
        if kind == "TEXT" and not isinstance(value, str):
            raise TypeError(f"Invalid Python TEXT value for Logistics policy: {key}")

        source_ref = row.get("source_ref")
        if not isinstance(source_ref, str) or not source_ref:
            raise ValueError(f"Missing source_ref for Logistics policy: {key}")
        values[key] = value
        source_refs[key] = source_ref

    missing = _REQUIRED_POLICY_KEYS - values.keys()
    if missing:
        raise LookupError(
            f"Required Logistics policies were not found: {', '.join(sorted(missing))}"
        )
    # 선택 정책은 없어도 실패가 아니다 — None 으로 두면 해당 signal 판정만 꺼진다.
    for optional_key in _OPTIONAL_NUMERIC_POLICY_KEYS:
        values.setdefault(optional_key, None)

    for key in _INTEGER_POLICY_KEYS:
        raw = values.get(key)
        if raw is None:
            continue  # 선택 정책이 안 실렸다 — 쓰는 자리에서 막는다
        assert isinstance(raw, Decimal)
        if raw != raw.to_integral_value():
            raise ValueError(f"Logistics policy must be an integer: {key}")
        values[key] = int(raw)
    return LogisticsPolicy(
        **values,
        policy_version=LOGISTICS_POLICY_VERSION,
        usage_scope=USAGE_SCOPE,
        source_refs=source_refs,
    )


def schedule_source[Schedule: (InTransitItem, ScheduledQuantity)](
    stored_status: object, rows: list[Schedule]
) -> tuple[object, list[Schedule] | None]:
    """저장된 status 와 신규 표 결과를 하나로 맞춘다. **어휘를 안 바꾼다.**

    🔴 **`UNRESOLVED` 는 그대로 둔다.** 그 값은 *"그 축을 확인한 적이 없다"* 이고,
       신규 표가 0건이라고 **확인했다고 바꾸지 않는다** — 하지 않은 확인을 장부에
       적는 것이 된다 (`transition._merge_schedule` 이 지키는 그 규율이다).

    ★ 그 밖에는 **목록이 status 를 정한다.** 저장된 `CONFIRMED`/`CONFIRMED_ZERO` 를
      읽어 쓰면 그 칸이 업무 사실의 두 번째 정본이 된다.
    """
    if stored_status == UNRESOLVED_SOURCE:
        return UNRESOLVED_SOURCE, None
    return ("CONFIRMED" if rows else "CONFIRMED_ZERO"), rows


def inventory_lot_from_row(row: dict[str, object], *, as_of: date) -> InventoryLotSnapshot:
    received_at = row.get("received_at")
    quantity = row.get("remaining_qty_kg")
    operational_limit = row.get("operational_limit_days")
    medium_factor = row.get("medium_grade_factor")
    if not isinstance(received_at, date):
        raise TypeError("Inventory lot received_at must be a date")
    if isinstance(quantity, bool) or not isinstance(quantity, Decimal):
        raise TypeError("Inventory lot remaining_qty_kg must be a Decimal")
    # 보관한계는 **없을 수 있다** — `item_storage_policies.operational_limit_days` 는
    # nullable 이고, 같은 칸을 읽는 `_item_storage_policy_from_row` 는 이미 NULL 을
    # 부재로 받아 `None` 으로 보존한다. 같은 식을 쓰는 `turnover.freshness_days_of`
    # 도 한계가 없으면 `None` 을 돌려준다 — **셋이 같은 칸을 같게 읽어야 한다.**
    #
    # 🔴 **여기만 예외를 냈다.** 그 예외는 Repository 밖에서 회사 상태가 아니라
    #    실행 실패로 읽혀(`service/agent_read.load_read` → `_SnapshotLoadError` → `ERROR`),
    #    보관한계 미등록 한 건이 물류 에이전트 **네 mode 를 통째로 끈다.** 게다가
    #    `ERROR` 는 재시도 가치가 있는 쪽이라(`envelope.worth_retry`) 마스터가 풀리지
    #    않을 호출을 되풀이한다. 부재는 `None` 으로 답하는 것이 답이다.
    #
    # 🔴 0 · 평균 · 품목 기본값으로 메우지 않는다. 없는 것은 없는 것이다.
    if operational_limit is not None and not isinstance(operational_limit, int):
        raise TypeError("Inventory lot operational_limit_days must be an int")
    if isinstance(medium_factor, bool) or not isinstance(medium_factor, Decimal):
        raise TypeError("Inventory lot medium_grade_factor must be a Decimal")
    # 등급 의존 판단은 raw가 아니라 정규화 결과 기준이다 — raw `상품` 계열은
    # 정규화되지 않으므로(None) medium_grade_factor를 조용히 건너뛰지 않고,
    # 해석 불가 사실이 lots[].grade = None으로 드러난다.
    normalized_grade = normalize_grade(row.get("grade"))
    freshness_limit = operational_limit
    if freshness_limit is not None and normalized_grade == "중":
        freshness_limit = int(Decimal(freshness_limit) * medium_factor)
    return InventoryLotSnapshot(
        lot_id=row.get("lot_id"),
        item=row.get("item_name"),
        grade=normalized_grade,
        available_qty_kg=quantity,
        received_at=received_at,
        unit_cost_krw_per_kg=_lot_unit_cost(row),
        # ★ 한계를 모르면 **잔여도 모른다.** 경과일만으로는 셈이 서지 않는다.
        remaining_freshness_days=(
            None if freshness_limit is None else freshness_limit - (as_of - received_at).days
        ),
        # remaining 계산에 쓴 그 한계를 그대로 싣는다 — 신선도 잔여 비율의 분모는
        # operational_limit 원값이 아니라 이 값이어야 한다 (중 등급 왜곡 방지).
        # 🔴 **둘은 함께 없거나 함께 있다.** 한쪽만 실으면 받는 쪽이 남은 하나로
        #    역산하는데, 그 역산이 정확히 이 칸이 막으려던 왜곡이다.
        effective_freshness_limit_days=freshness_limit,
        status=row.get("status"),
        storage_zone=row.get("storage_zone"),
    )


def _lot_unit_cost(row: object) -> Decimal | None:
    """Lot 의 실제 취득단가. **없으면 `None` 이다 — 0 으로 메우지 않는다.**

    🔴 매입 평균단가나 최근 단가로 추정하지 않는다. 장부에 없는 원가가 판정에
       들어가면 그 사고는 에러 없이 **마진만** 바꾼다.

    ★ 못 읽은 것을 예외로 올리지 않는다. 단가는 원가 기준에만 쓰이고 판매가능 판정에는
      안 쓰이므로, 한 Lot 의 단가가 없다고 물류 조회 전체를 세우면 상관없는 축이
      화면을 비운다. 없는 채로 두면 그 Lot 을 쓰는 원가 배부가 fail-closed 된다.
    """
    value = row.get("unit_cost_krw_per_kg") if hasattr(row, "get") else None
    if isinstance(value, bool) or not isinstance(value, Decimal):
        return None
    if not value.is_finite() or value < 0:
        return None
    return value


def single_fixture_row(
    rows: list[dict[str, Any]], *, as_of: date, sim_run_id: str | None
) -> dict[str, Any]:
    """그날 fixture 는 **정확히 한 건**이어야 한다 — 0건은 부재(LookupError), 2건 이상은 무결성
    위반.
    """
    실행 = "" if sim_run_id is None else f", sim_run_id={sim_run_id}"
    if not rows:
        raise LookupError(f"No active Logistics runtime fixture for as_of={as_of}{실행}")
    if len(rows) > 1:
        raise ValueError(
            f"Expected exactly one active Logistics runtime fixture, found {len(rows)}{실행}"
        )
    return rows[0]


def check_fixture_row(
    row: dict[str, object], *, expected_as_of: date, expected_sim_run_id: str | None
) -> None:
    """읽어 온 fixture 행이 **물어본 날·범위·실행의 행**인가. 아니면 멈춘다. DB 를 만지지 않는다."""
    if row.get("as_of") != expected_as_of:
        raise ValueError("Logistics runtime fixture as_of mismatch")
    if row.get("usage_scope") != USAGE_SCOPE:
        raise ValueError("Logistics runtime fixture usage_scope mismatch")
    # 🔴 **읽어 온 행이 물어본 실행의 행인지 다시 본다.** 위 두 줄과 같은 규율이다 —
    #    WHERE 가 조용히 빠지면 이 검사가 그 순간을 잡는다. `fixture.sim_run_id` 는
    #    바로 아래에서 `inventory_lots` · `outbound_commitments` 조회 열쇠가 되므로,
    #    여기서 안 잡으면 **한 스냅샷 안에 두 실행의 사실이 섞인다.**
    if expected_sim_run_id is not None and row.get("sim_run_id") != expected_sim_run_id:
        raise ValueError("Logistics runtime fixture sim_run_id mismatch")


def runtime_fixture_from(
    row: dict[str, object],
    *,
    in_transit: list[InTransitItem],
    confirmed_inbound: list[ScheduledQuantity],
    confirmed_outbound: list[ScheduledQuantity],
) -> LogisticsRuntimeFixture:
    """fixture Header 행 + 업무 정본에서 읽은 세 목록 → 계약 타입. **status 어휘를 안 바꾼다.**

    DB 를 만지지 않는다 — 목록을 읽는 것은 부르는 쪽(`_schedule_lists`)이다.
    """
    in_transit_status, in_transit_list = schedule_source(
        row.get("in_transit_status"), in_transit
    )
    confirmed_status, confirmed_list = schedule_source(
        row.get("confirmed_inbound_status"), confirmed_inbound
    )
    outbound_status, outbound_list = schedule_source(
        row.get("confirmed_outbound_status"), confirmed_outbound
    )
    fixture = LogisticsRuntimeFixture(
        fixture_id=row.get("fixture_id"),
        sim_run_id=row.get("sim_run_id"),
        as_of=row.get("as_of"),
        in_transit_status=in_transit_status,
        in_transit=in_transit_list,
        confirmed_inbound_status=confirmed_status,
        confirmed_inbound_schedule=confirmed_list,
        confirmed_outbound_status=outbound_status,
        confirmed_outbound_schedule=outbound_list,
        usage_scope=row.get("usage_scope"),
        evidence_grade=row.get("evidence_grade"),
        source_ref=row.get("source_ref"),
        approved_by=row.get("approved_by"),
    )
    return fixture
