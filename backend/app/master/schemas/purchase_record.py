"""실매입 기록 요청 · 응답 모델."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# ── 실매입 기록 (설계 260915 안 A) ─────────────────────────────────────


class PurchaseRecordLegIn(BaseModel):
    """실매입 한 회차. 선정안 회차(`seq`)마다 하나다.

    사람은 금액이 아니라 단가(원/kg, 정수)를 적는다. 회차 금액은 수량 × 단가로 계산한다.
    금액을 받으면 원장이 금액 ÷ 수량으로 단가를 만들어 소수가 생긴다. 예를 들어 480kg ·
    275,000원이면 단가가 572.916667 이 된다. 이 단가를 원장에서 반올림할 수도 없다 — DB
    CHECK `|line_amount_krw − quantity_kg × unit_price_krw_per_kg| < 0.1` 때문에 573 으로
    올리면 480 × 573 = 275,040 이라 40원 차이로 거부된다. 그래서 입구에서 정수 수량과
    정수 단가를 받고, 원장 단가는 적은 단가 그대로 남는다.

    금액 칸은 받지 않는다. 금액을 함께 받으면 수량 × 단가와 어긋날 때 어느 쪽이 실제로
    산 값인지 정할 수 없다.

    제약: `arrival_date` 는 `purchase_date` 보다 앞설 수 없다.
    """

    seq: int
    qty_kg: int = Field(gt=0)
    unit_price_krw: int = Field(
        gt=0,
        description=(
            "원/kg, 정수. 회차 금액은 qty_kg × unit_price_krw 로 계산하며, 매입 원장의 "
            "단가는 이 값 그대로 남는다."
        ),
    )
    purchase_date: date
    arrival_date: date

    @property
    def amount_krw(self) -> int:
        """회차 금액. 수량 × 단가다 — 받는 값이 아니라 나는 값이다.

        화면은 이 값을 읽기 전용으로 보여 주고, 아래(약정 사본 · 기록 표 · 원장)로는 이
        금액이 흐른다.
        """
        return self.qty_kg * self.unit_price_krw

    @model_validator(mode="after")
    def _arrival_not_before_purchase(self) -> PurchaseRecordLegIn:
        if self.arrival_date < self.purchase_date:
            raise ValueError(
                f"{self.seq}회차 도착일({self.arrival_date})이"
                f" 매입일({self.purchase_date})보다 앞선다."
            )
        return self


class PurchaseRecordIn(BaseModel):
    """`POST /master/runs/{request_id}/purchase-record` 요청 본문.

    회차 수와 `seq` 는 선정안 그대로다 — 사람은 값만 고친다. 회차 추가 · 삭제 · 부분
    기록은 받지 않으며(선정안 회차와 다르면 거절), 시장 · 메모 칸은 없다.
    같은 회차를 두 번 적을 수 없고, 등급과 기록자는 비어 있으면 안 된다.
    """

    decision_seq: int
    grade: str = Field(min_length=1)
    recorded_by: str = Field(min_length=1)
    legs: list[PurchaseRecordLegIn] = Field(min_length=1)

    @model_validator(mode="after")
    def _one_leg_per_seq(self) -> PurchaseRecordIn:
        if not self.grade.strip():
            raise ValueError("등급이 비어 있다.")
        if not self.recorded_by.strip():
            raise ValueError("기록자가 비어 있다.")
        seqs = [leg.seq for leg in self.legs]
        if len(set(seqs)) != len(seqs):
            raise ValueError(f"같은 회차가 두 번 적혔다: {seqs}")
        return self


class PurchaseRecordLegOut(BaseModel):
    """회차 한 줄. 선정안 값(`plan`)과 기록값(`record`)이 같은 모양이다."""

    seq: int
    qty_kg: float

    unit_price_krw: float | None = None
    """원/kg. 폼이 미리 채우는 값이고 사람이 고치는 칸이다.

    선정안 쪽(`plan.legs[]`)은 안의 `sourcing_plan[].grade_unit_price` 에서 온다 — 안에
    단가가 없거나 등급 줄이 여럿이면 `None` 이다. 금액 ÷ 수량으로 지어내지 않는다. 그
    값은 선정안이 적은 단가가 아니라 마스터가 만든 숫자다.

    기록 쪽(`record.legs[]`)은 `master_purchase_records.amount_krw ÷ quantity_kg` 다. 입력이
    단가라 저장된 금액이 수량 × 단가이므로 이 나눗셈은 정확히 정수로 떨어진다 — 그래서
    표에 칸을 더하지 않는다 (`readmodel/purchase_record.py`).
    """

    amount_krw: float | None = None
    """회차 금액. 수량 × 단가로 난 값이다 — 사람이 적는 칸이 아니다.

    화면이 확인용으로 보여 준다. 입력의 주인은 `unit_price_krw` 하나다.
    """

    purchase_date: date
    arrival_date: date


class PurchaseRecordPlanOut(BaseModel):
    """화면이 실매입 기록 폼에 미리 채울 선정안 값."""

    grade: str | None = None
    legs: list[PurchaseRecordLegOut] = Field(default_factory=list)


class PurchaseRecordValuesOut(BaseModel):
    """적힌 실매입 기록."""

    grade: str
    recorded_by: str
    recorded_at: datetime
    legs: list[PurchaseRecordLegOut] = Field(default_factory=list)


PurchaseRecordStatus = Literal["AWAITING_PURCHASE_RECORD", "APPLIED", "NOT_APPLIED", "NOT_REQUIRED"]

#: 실매입 기록의 반영 상태 (`GET …/purchase-record`).
#:
#:   ```text
#:   AWAITING_PURCHASE_RECORD   사람 승인 · 기록 없음 → 폼
#:   APPLIED                    기록 있음 · 매입 원장에 닿았다
#:   NOT_APPLIED                기록 있음 · 아직 원장에 없다 → 다음 개장 뒤 재시도가 기록값으로
#:   NOT_REQUIRED               자동 승인(AUTO-BACKFILL) · 기록 대상이 아니다
#:   ```


class PurchaseRecordOut(BaseModel):
    """`GET /master/runs/{request_id}/purchase-record` 응답 — 화면용."""

    request_id: str
    decision_seq: int
    scenario_label: str | None = None
    decided_by: str
    status: PurchaseRecordStatus
    reason: str = ""
    plan: PurchaseRecordPlanOut
    record: PurchaseRecordValuesOut | None = None
