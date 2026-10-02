"""재고·물류 Agent 판정 계약.

이 파일은 에이전트 계약(판정 · 시나리오 결과 · 재고 집계 모델)을 둔다. 스냅샷 · 정책 · fixture 는
`schemas/snapshot.py`, 화면 조회 응답(`Console*`)은 `schemas/console.py` 에 있다. 출고 · 회전
어휘는 `schemas/outbound.py` · `schemas/turnover.py` 에서 읽는다 — 계약 모듈이 쓰기 코어를
import 하지 않는다.
"""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.logistics.schemas.snapshot import reject_boolean
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
    """확정 판매 물량에 FEFO 로 배부된 예상 재고 취득원가.

    물류가 소유하는 모양이다. 재무 `InventoryCostBasis` 를 import 하지 않는다 —
    실행 계층에서 두 Agent 를 붙이면 마스터가 중개할 자리가 사라지고, 재무가 판정
    필드를 하나 바꾸는 날 물류 계산이 조용히 따라 바뀐다. 칸 이름만 같게 둔다.

    PRE_SALES 시점의 예상이지 출고 사실이 아니다. 이 값이 서는 자리는 판매 제안
    전이고, 그 판매의 예약도 할당도 아직 없다 (승인 → 예약 →
    `fefo_allocation` → 출고 순서다). 그래서 여기 담긴 Lot 은 "지금 출고한다면
    FEFO 가 집을 Lot" 이다.

    그래서 순서만이라도 실제와 같아야 한다. 정렬 키는 실제 자동 출고와
    같은 `turnover.fefo_sort_key` 하나다.

    `allocation_method` 와 `cost_method` 는 다른 축이다. 앞은 "어느 Lot 을 어떤
    순서로 고르나"(FEFO)이고 뒤는 "그 Lot 의 단가가 무엇이었나"(ACTUAL)다. 하나로
    합치면 «FEFO 로 골랐으니 원가도 FEFO 다» 같은, 장부에 없는 원가가 생긴다.

    `source_refs` 가 정본이다. `source_ref` 는 하위 호환용 대표 하나일 뿐이라
    두 Lot 에 걸친 배부 근거를 그것만으로는 따라갈 수 없다.
    """

    model_config = ConfigDict(extra="forbid")

    item: str = Field(min_length=1)
    #: 이 원가가 덮는 양. 확정 물량과 정확히 같을 때만 기준이 선다.
    quantity_kg: Decimal = Field(ge=0)
    amount_krw: Decimal = Field(ge=0)
    #: Lot 선택 순서. 실제 자동 출고와 같은 FEFO 한 가지다 —
    #: 없는 방식을 이름으로 만들지 않는다.
    allocation_method: Literal["FEFO"] = "FEFO"
    #: 단가의 성격. 장부 실단가를 그대로 썼다는 사실이다.
    cost_method: Literal["ACTUAL"] = "ACTUAL"
    included_components: tuple[str, ...] = ("inventory_acquisition_cost",)
    #: 이 금액을 배부하는 데 쓴 Lot 근거, FEFO 배부 순서 그대로.
    #:
    #: «출고된 Lot» 도 «헐어 쓴 Lot» 도 아니다. PRE_SALES 는 할당 전이라
    #: 출고 사실이 아직 없다. 대표 하나로 줄이지 않는다.
    source_refs: tuple[str, ...] = Field(min_length=1)
    evidence_grade: str = Field(min_length=1)

    @property
    def source_ref(self) -> str:
        """하위 호환용 대표 ref. 배부 근거 전체가 아니다 — 전체는 `source_refs` 다."""
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
