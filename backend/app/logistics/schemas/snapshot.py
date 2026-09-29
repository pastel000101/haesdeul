"""물류가 한 호출 동안 고정해 읽는 사실 — 스냅샷 · 정책 · Runtime fixture · 입고/출고 일정 한 줄.

★ 2026-09-30 재구성 BL-015: `logistics/schemas.py` 의 스냅샷 절을 옮겼다. 한 호출이 읽은 한
  벌(`LogisticsRead`)은
  `schemas/current.py`. 채우는 쪽은 `readmodel/current.py`, 규칙은 `domain/snapshot.py`.
"""

from datetime import date
from decimal import Decimal
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: 물류 운영 Policy 의 현재 버전. **문서 세트 버전(v1.4)과 다른 축이다** — 이쪽은
#: `agent_policy_config` 의 행을 고르는 값이라 DB 와 함께 움직인다.
#: 타입(Literal)과 값이 같이 가야 하므로 한 곳에서 만든다 — 종전에는 이 문자열이
#: repository 상수 1곳 + Literal 3곳으로 흩어져 버전을 올릴 때 네 곳을 동시에
#: 고쳐야 했다 (#121 ⑤).
PolicyVersion = Literal["v1.3-PROVISIONAL"]
#: 값은 타입에서 **파생한다** — 문자열이 한 번만 적히게 하려는 것이다. 둘을 나란히
#: 적으면 버전을 올릴 때 여전히 두 줄을 함께 고쳐야 한다 (2026-09-01 교차검증 지적).
POLICY_VERSION: PolicyVersion = get_args(PolicyVersion)[0]


RuntimeSourceStatus = Literal["CONFIRMED", "CONFIRMED_ZERO", "UNRESOLVED"]

#: *"그 축을 확인한 적이 없다"* 를 뜻하는 값. 🔴 **이 문자열을 여기 말고 어디에도
#: 다시 적지 않는다** — 판정하는 자리가 둘(`domain/snapshot.schedule_source` 화면 축 ·
#: `inbound_stock.load_in_transit_for_receiving` 도착 축)이라, 한쪽만 고쳐지는 날
#: 같은 날 같은 입고를 두 경로가 다르게 읽는다.
UNRESOLVED_SOURCE: RuntimeSourceStatus = "UNRESOLVED"


def reject_boolean(value: object) -> object:
    if isinstance(value, bool):
        raise ValueError("boolean values are not valid numeric inputs")  # noqa: TRY004
    return value


class ScheduledQuantity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    quantity_kg: Decimal = Field(ge=0)
    item: str | None = None
    #: B-1 입고 건 식별자. outbound 등 다른 Schedule에서도 이 모델을 재사용하므로
    #: 전역 필수값이 아니다 — in_transit 정합성 검증에서만 존재 여부를 판단한다.
    inbound_id: str | None = None

    @field_validator("quantity_kg", mode="before")
    @classmethod
    def reject_boolean_quantity(cls, value: object) -> object:
        return reject_boolean(value)


class InventoryLotSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lot_id: str = Field(min_length=1)
    item: str = Field(min_length=1)
    #: Purchase용 정규화 등급(특/상/중/하). 정규화 근거가 없으면 None —
    #: raw `상품`을 근거 없이 `상`으로 바꾸지 않는다.
    grade: str | None = None
    available_qty_kg: Decimal = Field(ge=0)
    #: 이 Lot 이 창고에 들어온 날. **FIFO 배부 순서의 축**이다.
    #:
    #: ★ 신선도 계산에 이미 쓰던 사실이라 새로 만드는 값이 아니다 — 그동안 스냅샷에
    #:   싣지 않았을 뿐이다.
    received_at: date | None = None
    #: 이 Lot 의 **실제 취득단가**(원/kg). `inventory_lots.unit_cost_krw_per_kg` 그대로다.
    #:
    #: 🔴 **재계산하지 않는다.** 매입 평균단가나 최근 단가로 추정하면 그 순간 장부에
    #:    없는 원가가 판정에 들어간다. 못 읽으면 `None` 이고, 그때 원가 기준은 서지
    #:    않는다 — 0원으로 메우지 않는다.
    unit_cost_krw_per_kg: Decimal | None = Field(default=None, ge=0)
    remaining_freshness_days: int | None = None
    #: remaining_freshness_days 계산에 실제 사용된 유효 보관한계.
    #: `중` 등급은 operational_limit × medium_grade_factor 가 유효 한계이므로,
    #: 신선도 잔여 비율의 분모로 operational_limit 원값을 쓰면 갓 입고된 중 등급이
    #: 즉시 임박 판정된다 — Rule 이 재계산하지 않도록 계산 주체가 여기 실어 준다.
    effective_freshness_limit_days: int | None = None
    status: str = Field(min_length=1)
    storage_zone: str | None = None

    @field_validator(
        "available_qty_kg",
        "remaining_freshness_days",
        "effective_freshness_limit_days",
        mode="before",
    )
    @classmethod
    def reject_boolean_numbers(cls, value: object) -> object:
        return reject_boolean(value)


class InTransitItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: B-1: 행이 존재하면 confirmed_inbound_schedule과 같은 건인지 이 ID로 대조한다.
    inbound_id: str | None = None
    #: 이 운송 건이 **어느 매입에서 왔나**. 도착 시점에 이 참조로 `purchase_items` 를
    #: 읽어 `purchase_item_id` · `item_id` · `grade` · `unit_price_krw_per_kg` 를
    #: **그때의 권위값**으로 가져오려는 자리다.
    #:
    #: 🟡 **받을 자리는 뚫려 있지만 아직 안 켜졌다 (2026-09-05).** `purchase_id` 를
    #:    만드는 곳은 마스터(`app/master/transition.py` 의 `purchase_id_for`)이고,
    #:    그 값이 물류로 넘어오려면 **마스터 전이 규약
    #:    (`LogisticsTransition.build`)이 바뀌어야 한다.** 그것은 마스터 소유
    #:    파일이라 물류가 고칠 자리가 아니다 — **후속 협의 안건**이다.
    #:
    #:    ```text
    #:    지금      transition.build_next_inventory(commitment)                 → None
    #:    협의 뒤    transition.build_next_inventory(…, purchase_ids={seq: id})  → 그 값
    #:    ```
    #:
    #:    물류 쪽 준비는 끝났다 — `purchase_ids` 인자가 기본값 `None` 으로 이미 있고,
    #:    마스터가 넘겨 주는 날 값이 그대로 실린다.
    #:
    #: ★ **물류가 이 ID 를 지어내지 않는다.** 같은 규칙으로 다시 조립하면 같은
    #:   사실의 주인이 둘이 되고, 마스터가 형식을 바꾸는 날 두 곳이 어긋난 채로
    #:   조용히 돈다. 받아서 보관하는 것 외의 경로를 만들지 않는다.
    #:
    #: 🔴 **`inbound_id` 를 대신하지 않는다.** 둘은 다른 정체성이다 —
    #:    `inbound_id` 는 *"물류가 셈하는 입고 건"*, `purchase_id` 는 *"매입 원장의
    #:    어느 행에서 왔나"* 다. B-1 대조의 열쇠는 여전히 `inbound_id` 다.
    #:
    #: ★ **여기에 매입 사실을 복제하지 않는다.** `item_id` · `grade` ·
    #:   `unit_price_krw_per_kg` 는 `purchase_items` 가 주인이다. 복사해 두면 매입이
    #:   값을 고치는 날 이쪽만 옛 값을 들고 남는다.
    #:
    #: ⚠️ **읽기 계약은 관대하다 (`None` 허용).** 이 필드가 생기기 전에 적힌 fixture
    #:    행에는 이 키가 없다 — 전역 필수로 올리면 그 행들이 통째로 파싱에 실패해
    #:    물류가 `RUNTIME_NOT_READY` 로 돌아선다.
    purchase_id: str | None = None
    item: str = Field(min_length=1)
    quantity_kg: Decimal = Field(gt=0)
    expected_arrival_date: date | None

    @field_validator("quantity_kg", mode="before")
    @classmethod
    def reject_boolean_quantity(cls, value: object) -> object:
        return reject_boolean(value)


class ItemStoragePolicyFact(BaseModel):
    """품목 자체의 보관 정책. Lot의 현재 상태와 다른 개념이다.

    `remaining_freshness_days`는 *"이 Lot이 앞으로 며칠 쓸 수 있나"*이고
    `operational_limit_days`는 *"이 품목이 원래 며칠 보관 가능한가"*다.
    새로 매입하는 물량의 기준은 후자이므로 기존 Lot의 잔여일수에서 역산하면 안 된다.
    """

    model_config = ConfigDict(extra="forbid")

    item: str = Field(min_length=1)
    #: DB에 값이 없으면 None을 유지한다 — 코드에서 기본값을 만들지 않는다.
    operational_limit_days: int | None = None
    #: 등급 사다리(#69)가 정리되기 전까지 의미를 재정의하지 않고 DB Fact 그대로 나른다.
    #: ★ "재정의하지 않는다" = 물류가 해석·rename·재계산을 얹지 않는다는 뜻이다.
    #:   소비자(매입)가 자기 계산에 쓰는 것을 막는 뜻이 아니다 — 값은 MVP 정책값
    #:   (DB note)이고, #69 가 정하는 것은 계수 값이 아니라 곱하는 대상 등급이다.
    medium_grade_factor: Decimal | None = None

    @field_validator("operational_limit_days", "medium_grade_factor", mode="before")
    @classmethod
    def reject_boolean_numbers(cls, value: object) -> object:
        return reject_boolean(value)


class OutboundCommitment(BaseModel):
    """출고가 **이미 잡아 둔** 몫 한 줄. 아직 창고에는 있지만 남에게 팔 수 없는 양이다.

    ```text
    lot_id 있음   그 Lot 에 붙은 살아있는 할당 (ALLOCATED · PICKED)
    lot_id 없음   아직 Lot 을 안 고른 예약의 미할당 잔여
    ```

    🔴 **`SHIPPED` 는 여기 없다.** 나간 몫은 원장 OUT 이 `remaining_qty_kg` 에서 이미
       덜어냈다 — 다시 빼면 같은 수량을 두 번 차감한다 (`outbound.py` 와 같은 규율).
    """

    model_config = ConfigDict(extra="forbid")

    item: str = Field(min_length=1)
    #: `None` 은 **Lot 미지정 예약**이다. 없는 Lot 을 가리키는 것이 아니다.
    lot_id: str | None = None
    quantity_kg: Decimal = Field(gt=0)

    @field_validator("quantity_kg", mode="before")
    @classmethod
    def reject_boolean_quantity(cls, value: object) -> object:
        return reject_boolean(value)


class InventoryLogisticsSnapshot(BaseModel):
    """Repository가 한 호출 진입 시점에 읽어 고정한 Inventory/Logistics 사실과 정책값.

    ★ **폐지된 T0 스냅샷이 아니다.** 마스터가 전 부서 데이터를 얼려 배포하던 구조는
      정의서 v2.5 §3.2 로 폐지됐다. 이 모델은 물류가 **자기 도메인만** 1회 읽어
      호출이 끝날 때까지 고정하는 값이며, 정의서 §1.2-13 이 요구하는 것이다.
      (`repository` 모듈 docstring 참조)
    """

    model_config = ConfigDict(extra="forbid")

    #: 🔴 **폐지된 T0 스냅샷의 식별자 — 실제 유산이다.** Repository 는 항상 `None` 을
    #: 넣고, 그래서 `_snapshot_warnings` 가 매 실행마다 `SNAPSHOT_ID_UNRESOLVED` 를
    #: 낸다(상시 노이즈). 실행이력 컬럼·독립 응답 필드로도 나가 있어 그냥 지울 수
    #: 없다 — 실제 ID 를 부여할지 계약에서 걷어낼지가 **미결 안건**이다 (#121 별도).
    snapshot_id: str | None
    as_of: date
    on_hand_by_lot: list[InventoryLotSnapshot]
    #: 품목 단위 보관 정책. Lot 목록에서 역산하지 않으므로 재고 0kg인 품목도 들어온다.
    #: None은 미조회, []는 정책 0건 확인이다.
    item_storage_policies: list[ItemStoragePolicyFact] | None = None
    in_transit: list[InTransitItem] | None
    confirmed_inbound_schedule: list[ScheduledQuantity] | None
    confirmed_outbound_schedule: list[ScheduledQuantity] | None
    #: 🔴 **출고가 이미 잡아 둔 몫** (`inventory_reservations` · `inventory_allocations`).
    #: `None` 은 미조회, `[]` 는 **0건 확인**이다 — 둘을 뭉개면 예약을 못 읽은 것이
    #: *"예약이 없다"* 로 둔갑해 같은 재고가 두 번 팔린다.
    #:
    #: ⚠️ `confirmed_outbound_schedule` 과 **다른 축이다.** 저쪽은 fixture 가 적어 둔
    #:    확정 출고이고 이쪽은 WMS 표의 예약·할당이다. 실측(2026-09-05) 상 fixture 쪽은
    #:    전부 `CONFIRMED_ZERO` 라 지금은 겹치지 않는다 — fixture 에 실제 값이 들어오는
    #:    날 **한 축으로 합쳐야 한다** (지금 둘을 다 빼면 이중 차감이다).
    outbound_commitments: list[OutboundCommitment] | None = None
    used_capacity_kg: Decimal = Field(ge=0)
    guaranteed_capacity_kg: Decimal | None = Field(default=None, gt=0)
    burst_capacity_kg: Decimal | None = Field(default=None, gt=0)
    guaranteed_capacity_by_zone_kg: dict[str, Decimal] | None
    inbound_lead_days: int | None = Field(default=None, ge=0)
    daily_inbound_capacity_kg: Decimal | None = Field(default=None, gt=0)
    inbound_transport_capacity_kg: Decimal | None = Field(default=None, gt=0)
    shared_daily_outbound_capacity_kg: Decimal | None = Field(default=None, gt=0)
    #: 선택 정책 2종 (LLM 정책 결정서 §4). 없으면 해당 업무 위험 판정만 SKIPPED 되고
    #: 물류 계산은 정상 수행한다 — 필수 키로 승격 금지(행 없는 순간 전체 실패).
    capacity_tight_ratio: Decimal | None = Field(default=None, gt=0, le=1)
    freshness_pressure_ratio: Decimal | None = Field(default=None, gt=0, le=1)
    policy_version: PolicyVersion = POLICY_VERSION
    evidence_refs: list[str]

    @field_validator(
        "used_capacity_kg",
        "guaranteed_capacity_kg",
        "burst_capacity_kg",
        "inbound_lead_days",
        "daily_inbound_capacity_kg",
        "inbound_transport_capacity_kg",
        "shared_daily_outbound_capacity_kg",
        "capacity_tight_ratio",
        "freshness_pressure_ratio",
        mode="before",
    )
    @classmethod
    def reject_boolean_numbers(cls, value: object) -> object:
        return reject_boolean(value)


class LogisticsPolicy(BaseModel):
    """Logistics MVP 실행에 사용하는 운영 제약 및 정책."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    guaranteed_capacity_kg: Decimal = Field(gt=0)
    burst_capacity_kg: Decimal = Field(gt=0)
    inbound_lead_days: int = Field(ge=0)
    daily_inbound_capacity_kg: Decimal = Field(gt=0)
    inbound_transport_capacity_kg: Decimal = Field(gt=0)
    shared_daily_outbound_capacity_kg: Decimal = Field(gt=0)
    cap_by_date_policy: Literal["CONFIRMED_ONLY"]
    #: 출고 준비에 걸리는 날 수 (WP-4 M4). `as_of + 이 값` 이 가장 이른 납기일이다.
    #:
    #: 🔴 **선택 정책이지만 «없으면 1» 이 아니다.** 행이 없으면 `None` 이고, PRE_SALES
    #:    는 그때 `delivery_feasibility.status = "UNRESOLVED"` 로 **답을 안 낸다** —
    #:    코드 상수 `1` 로 메우면 DB 에 정책이 없는데도 납기가 확정된 것처럼 나간다.
    #:
    #: ⚠️ **필수로 올리지 않았다.** 올리면 행이 없는 순간 물류가 통째로
    #:    `RUNTIME_NOT_READY` 가 되어 입고·Capacity 처럼 납기와 **무관한 경로까지**
    #:    멈춘다 (`_OPTIONAL_NUMERIC_POLICY_KEYS` 주석이 같은 이유를 적고 있다).
    #:    fail-closed 는 그 값을 실제로 쓰는 자리에서 건다.
    outbound_prep_lead_days: int | None = Field(default=None, ge=0)
    #: 선택 정책 — DB에 행이 없으면 None 이며 해당 signal 판정만 꺼진다.
    #: 값의 성격은 실업계 기준이 아니라 시뮬레이션 검증용 PROVISIONAL 이다.
    capacity_tight_ratio: Decimal | None = Field(default=None, gt=0, le=1)
    freshness_pressure_ratio: Decimal | None = Field(default=None, gt=0, le=1)
    policy_version: PolicyVersion
    usage_scope: Literal["AGENT_MVP_DEMO"]
    source_refs: dict[str, str]

    @field_validator(
        "guaranteed_capacity_kg",
        "burst_capacity_kg",
        "inbound_lead_days",
        "daily_inbound_capacity_kg",
        "inbound_transport_capacity_kg",
        "shared_daily_outbound_capacity_kg",
        "outbound_prep_lead_days",
        "capacity_tight_ratio",
        "freshness_pressure_ratio",
        mode="before",
    )
    @classmethod
    def reject_boolean_policy_numbers(cls, value: object) -> object:
        return reject_boolean(value)


class LogisticsRuntimeFixture(BaseModel):
    """AGENT_MVP_DEMO 전용 Logistics schedule completeness fixture."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fixture_id: str = Field(min_length=1)
    sim_run_id: str = Field(min_length=1)
    as_of: date
    in_transit_status: RuntimeSourceStatus
    in_transit: list[InTransitItem] | None
    confirmed_inbound_status: RuntimeSourceStatus
    confirmed_inbound_schedule: list[ScheduledQuantity] | None
    confirmed_outbound_status: RuntimeSourceStatus
    confirmed_outbound_schedule: list[ScheduledQuantity] | None
    usage_scope: Literal["AGENT_MVP_DEMO"]
    evidence_grade: Literal["SIM_FIXED"]
    source_ref: str = Field(min_length=1)
    approved_by: Literal["HUMAN"]

    @model_validator(mode="after")
    def validate_schedule_statuses(self) -> "LogisticsRuntimeFixture":
        sources = (
            ("in_transit", self.in_transit_status, self.in_transit),
            (
                "confirmed_inbound",
                self.confirmed_inbound_status,
                self.confirmed_inbound_schedule,
            ),
            (
                "confirmed_outbound",
                self.confirmed_outbound_status,
                self.confirmed_outbound_schedule,
            ),
        )
        for name, status, schedule in sources:
            if status == "UNRESOLVED" and schedule is not None:
                raise ValueError(f"{name} UNRESOLVED must preserve None")
            if status == "CONFIRMED_ZERO" and schedule != []:
                raise ValueError(f"{name} CONFIRMED_ZERO must have an empty list")
            if status == "CONFIRMED" and not schedule:
                raise ValueError(f"{name} CONFIRMED must have confirmed rows")
        if (
            self.in_transit_status == "CONFIRMED"
            and self.in_transit is not None
            and any(item.expected_arrival_date is None for item in self.in_transit)
        ):
            raise ValueError("confirmed in_transit rows require expected_arrival_date")
        return self


#: 계약(Literal)과 같은 값을 쓴다 — schemas 가 단일 소유다 (#121 ⑤).
LOGISTICS_POLICY_VERSION = POLICY_VERSION
