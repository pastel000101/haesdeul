"""재고·물류 Agent A/B 요청, Snapshot 및 응답 계약.

★ 2026-09-30 재구성 BL-015: `logistics/schemas.py` 가 `schemas/` 폴더가 되며 에이전트
  계약(판정 · 회신 · 독립 사이클 요청/응답)이 이 파일로 왔다. 스냅샷 · 정책 · fixture 는
  `schemas/snapshot.py`, 화면 조회 응답(`Console*`)은 `schemas/console.py` 로 갈랐다. 출고 · 회전
  어휘는 `schemas/outbound.py` · `schemas/turnover.py` 에서 읽는다 — 계약 모듈이 쓰기 코어를 import
  하던 역방향이 없어졌다.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from app.logistics.llm.schemas import LLMResponseFields
from app.logistics.schemas.snapshot import POLICY_VERSION, PolicyVersion, reject_boolean
from app.purchase_agent.schemas.proposal import PurchaseProposal

RuntimeStatus = Literal["READY", "RUNTIME_NOT_READY", "ERROR"]
FinalVerdict = Literal["PASS", "REVIEW_REQUIRED", "FAIL"]
RuleStatus = Literal["PASS", "UNRESOLVED", "FAIL"]
LogisticsCycle = Literal["PROCUREMENT", "SALES"]


ConstraintCode = Literal[
    "LOG-H01",
    "LOG-H02",
    "LOG-H03",
    "LOG-H04",
    "LOG-H05",
    "N17",
    "N17-LOT",
    "IN_TRANSIT_SCHEDULE_UNRESOLVED",
    "CONFIRMED_OUTBOUND_ITEM_UNRESOLVED",
    "AS_OF_MISMATCH",
    "REQUIRED_LOGISTICS_SNAPSHOT_MISSING",
]
ScenarioVerdict = Literal["ok", "conditional", "reject", "skipped"]
LogisticsReasonCode = Literal[
    "CAPACITY_EXCEEDED",
    "NO_FEASIBLE_ARRIVAL_DATE",
    "FRESHNESS_EXPIRED",
    "FRESHNESS_WARNING",
]
AdjustmentAxis = Literal["quantity", "timing"]


PurchaseAgentOutput = PurchaseProposal


class ConstraintResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ConstraintCode
    status: RuleStatus
    skip_reason: str | None = None


class InventoryCostBasisSnapshot(BaseModel):
    """확정 판매 물량에 FEFO 로 배부된 **예상 재고 취득원가**.

    ★ **물류가 소유하는 모양이다.** 재무 `InventoryCostBasis` 를 import 하지 않는다 —
      실행 계층에서 두 Agent 를 붙이면 마스터가 중개할 자리가 사라지고, 재무가 판정
      필드를 하나 바꾸는 날 물류 계산이 조용히 따라 바뀐다. 칸 이름만 같게 둔다.

    🔴 **PRE_SALES 시점의 예상이지 출고 사실이 아니다.** 이 값이 서는 자리는 판매 제안
       **전**이고, 그 판매의 예약도 할당도 아직 없다 (승인 → 예약 →
       `fefo_allocation` → 출고 순서다). 그래서 여기 담긴 Lot 은 *"지금 출고한다면
       FEFO 가 집을 Lot"* 이다.

       ★ **그래서 순서만이라도 실제와 같아야 한다.** 정렬 키는 실제 자동 출고와
         같은 `turnover.fefo_sort_key` 하나다.

    🔴 **`allocation_method` 와 `cost_method` 는 다른 축이다.** 앞은 *"어느 Lot 을 어떤
       순서로 고르나"*(FEFO)이고 뒤는 *"그 Lot 의 단가가 무엇이었나"*(ACTUAL)다. 하나로
       합치면 «FEFO 로 골랐으니 원가도 FEFO 다» 같은, 장부에 없는 원가가 생긴다.

    🔴 **`source_refs` 가 정본이다.** `source_ref` 는 하위 호환용 대표 하나일 뿐이라
       두 Lot 에 걸친 배부 근거를 그것만으로는 따라갈 수 없다.
    """

    model_config = ConfigDict(extra="forbid")

    item: str = Field(min_length=1)
    #: 이 원가가 덮는 양. 확정 물량과 **정확히 같을 때만** 기준이 선다.
    quantity_kg: Decimal = Field(ge=0)
    amount_krw: Decimal = Field(ge=0)
    #: Lot 선택 순서. 실제 자동 출고와 같은 FEFO 한 가지다 —
    #: 없는 방식을 이름으로 만들지 않는다.
    allocation_method: Literal["FEFO"] = "FEFO"
    #: 단가의 성격. 장부 실단가를 그대로 썼다는 사실이다.
    cost_method: Literal["ACTUAL"] = "ACTUAL"
    included_components: tuple[str, ...] = ("inventory_acquisition_cost",)
    #: 이 금액을 배부하는 데 **쓴 Lot 근거**, FEFO 배부 순서 그대로.
    #:
    #: ⚠️ **«출고된 Lot» 도 «헐어 쓴 Lot» 도 아니다.** PRE_SALES 는 할당 전이라
    #:    출고 사실이 아직 없다. 대표 하나로 줄이지 않는다.
    source_refs: tuple[str, ...] = Field(min_length=1)
    evidence_grade: str = Field(min_length=1)

    @property
    def source_ref(self) -> str:
        """하위 호환용 대표 ref. **배부 근거 전체가 아니다** — 전체는 `source_refs` 다."""
        return self.source_refs[0]

    @field_validator("quantity_kg", "amount_krw", mode="before")
    @classmethod
    def reject_boolean_numbers(cls, value: object) -> object:
        return reject_boolean(value)


class InventoryByItem(BaseModel):
    """가용재고 정의를 적용한 품목별 자유재고 합계. 등급 축으로 나누지 않는다."""

    model_config = ConfigDict(extra="forbid")

    item: str = Field(min_length=1)
    available_qty_kg: Decimal = Field(ge=0)

    @field_validator("available_qty_kg", mode="before")
    @classmethod
    def reject_boolean_quantity(cls, value: object) -> object:
        return reject_boolean(value)


class ScenarioAdjustment(BaseModel):
    """물류 허용 조정 축은 quantity/timing뿐이다. amount/channel_mix는 반환하지 않는다."""

    model_config = ConfigDict(extra="forbid")

    axis: AdjustmentAxis
    #: 조정 대상 분할 회차의 매입 실행일 — 어느 split에 대한 제안인지 식별용.
    split_date: date
    suggested_qty_kg: Decimal | None = None
    #: 매입 실행일 역산은 Purchase 책임이라 도착일 기준으로만 제안한다.
    suggested_arrival_date: date | None = None


class ScenarioValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    verdict: ScenarioVerdict
    reason_codes: list[LogisticsReasonCode]
    adjustments: list[ScenarioAdjustment]


class LogisticsBand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cap_by_date: dict[date, Decimal]
    unit: Literal["kg"] = "kg"


class InboundConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inbound_lead_days: int | None
    daily_inbound_capacity_kg: Decimal | None
    inbound_transport_capacity_kg: Decimal | None


class LogisticsEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_id: str = Field(min_length=1)
    claim: str = Field(min_length=1)


class LogisticsProcurementResponse(LLMResponseFields):
    model_config = ConfigDict(extra="forbid")

    agent: Literal["inventory_logistics"] = "inventory_logistics"
    cycle: Literal["PROCUREMENT"] = "PROCUREMENT"
    as_of: date
    snapshot_id: str | None
    policy_version: PolicyVersion = POLICY_VERSION
    runtime_status: RuntimeStatus
    #: 시나리오 집계 ⊕ 하드 제약의 최악값 결합 (2026-09-01 마스터 확정 · #121 3단계).
    #: any reject → FAIL / any conditional → REVIEW_REQUIRED / 전부 ok → PASS 에
    #: 하드 UNRESOLVED/FAIL 이 값을 낮출 수만 있다. 2026-09-01 이전 실행이력의
    #: verdict 는 하드 제약만의 판정이다.
    verdict: FinalVerdict | None
    band: LogisticsBand
    #: 물류가 직접 집계한 품목별 가용재고. confirmed_outbound.item 누락 등으로
    #: 정확히 계산할 수 없으면 None이며, 직렬화 시 키 자체를 뺀다 — `[]`(0건 확인)와
    #: 구분되어야 하기 때문이다. M-1 missing_data 번역은 Master Adapter 책임.
    inventory_by_item: list[InventoryByItem] | None = None
    scenario_results: list[ScenarioValidationResult] | None = None
    inbound_constraints: InboundConstraints
    hard_constraints: list[ConstraintResult]
    soft_warnings: list[str]
    #: 사람이 읽을 미확정 항목의 무숫자 번역명. soft_warnings(원본 기계 코드)와
    #: 채널을 분리한다 — 소비자가 AI 문장을 파싱하지 않고 바로 표시할 수 있고,
    #: LLM Context의 missing_data와 같은 어휘를 쓴다.
    missing_data: list[str] = Field(default_factory=list)
    #: Rule/Scenario Engine 이 결정한 우선 조정 축(quantity/timing). 조정이 없거나
    #: 축이 혼재하면 None — LLM 이 아니라 결정론 층이 정한 값이다.
    #: reject 시나리오의 조정은 집계에서 제외된다(#121 2단계) — 그 조정은
    #: scenario_results 안의 진단 기록으로만 남는다.
    preferred_adjustment: str | None = None
    evidences: list[LogisticsEvidence]

    @model_serializer(mode="wrap")
    def drop_uncomputable_inventory_by_item(self, handler: Any) -> dict:
        data = handler(self)
        if data.get("inventory_by_item") is None:
            data.pop("inventory_by_item", None)
        return data


class ArrivalScheduleItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    quantity_kg: Decimal = Field(gt=0)

    @field_validator("quantity_kg", mode="before")
    @classmethod
    def reject_boolean_quantity(cls, value: object) -> object:
        return reject_boolean(value)


class LogisticsApprovedPurchaseCommitment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(min_length=1)
    total_qty_kg: Decimal = Field(gt=0)
    expected_arrival_date: date
    arrival_schedule: list[ArrivalScheduleItem] = Field(min_length=1)

    @field_validator("total_qty_kg", mode="before")
    @classmethod
    def reject_boolean_total(cls, value: object) -> object:
        return reject_boolean(value)

    @model_validator(mode="after")
    def validate_arrival_total(self) -> "LogisticsApprovedPurchaseCommitment":
        scheduled_total = sum(
            (item.quantity_kg for item in self.arrival_schedule), start=Decimal(0)
        )
        if self.total_qty_kg != scheduled_total:
            raise ValueError("total_qty_kg must equal arrival_schedule quantity total")
        if self.expected_arrival_date != min(item.date for item in self.arrival_schedule):
            raise ValueError("expected_arrival_date must equal the first arrival schedule date")
        return self


class LogisticsSalesRequest(BaseModel):
    """Logistics B가 받는 H1 승인 매입 Delta."""

    model_config = ConfigDict(extra="forbid")

    cycle: Literal["SALES"]
    as_of: date
    approved_purchase: LogisticsApprovedPurchaseCommitment

    @model_validator(mode="after")
    def validate_arrival_dates(self) -> "LogisticsSalesRequest":
        if any(item.date < self.as_of for item in self.approved_purchase.arrival_schedule):
            raise ValueError("arrival_schedule dates must be on or after as_of")
        return self


class LotConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lot_id: str
    item: str
    available_qty_kg: Decimal = Field(ge=0)
    remaining_freshness_days: int | None = None
    #: Snapshot의 정규화 등급을 그대로 나른다. 정규화 근거가 없으면 None이며,
    #: 필드를 빠뜨리는 것(키 없음)과 None(확인 불가)은 다른 상태다.
    grade: str | None = None
    status: str


class LogisticsSalesResponse(LLMResponseFields):
    model_config = ConfigDict(extra="forbid")

    agent: Literal["inventory_logistics"] = "inventory_logistics"
    cycle: Literal["SALES"] = "SALES"
    snapshot_id: str | None
    approval_id: str
    runtime_status: RuntimeStatus
    verdict: FinalVerdict | None
    daily_outbound_capacity_kg: Decimal | None
    lot_constraints: list[LotConstraint]
    hard_constraints: list[ConstraintResult]
    soft_warnings: list[str]
    #: PRE와 같은 채널 분리 — 원본 기계 코드는 soft_warnings, 무숫자 번역명은 여기.
    missing_data: list[str] = Field(default_factory=list)
    #: Sales 에서 Rule 이 정한 우선 조정(현행 어휘: 우선 출고 검토 문장). LLM 이 아니라
    #: 결정론 층이 정한다 — 없으면 LLM 도 추천하지 않는다(검증기 강제).
    preferred_adjustment: str | None = None


class LogisticsAgentRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    cycle: LogisticsCycle
    as_of: date
    snapshot_id: str | None
    runtime_status: RuntimeStatus
    verdict: FinalVerdict | None
    request_payload: dict[str, object]
    response_payload: dict[str, object]
    created_at: datetime
