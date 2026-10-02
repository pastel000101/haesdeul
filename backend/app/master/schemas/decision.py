"""사용자 결정 요청 · 응답 모델과 결정 거절 예외."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.contracts.commitment import ApprovedCommitment
from app.master.schemas.sales_approval import SaleConfirmationOut
from app.master.schemas.transition import TransitionOut

Decision = Literal["APPROVE", "REJECT_ALL", "REQUEST_CHANGE", "CANCEL"]

#: `CANCEL` 은 `REJECT_ALL` 과 다르다 (2026-09-05 전원 합의).
#:
#:   ```text
#:   REJECT_ALL   "이 안을 안 쓴다"      — 승인 전 판단. 장부를 안 건드린다
#:   CANCEL       "승인했던 것을 물린다"  — 승인 후 사실. 장부 다섯을 되돌린다
#:   ```
#:
#: 둘을 한 어휘로 적으면 "거절해서 장부가 없는 것" 과 "취소해서 장부가 물린 것" 이
#:   같아진다.

RevalidationOutcome = Literal["PASSED", "CONDITIONAL", "FAILED", "ERROR"]


class DecisionRejected(ValueError):
    """결정을 받을 수 없다. 라우터가 409/422 로 접는다.

    조용히 무시하지 않는다. 받아 놓고 안 적으면 사용자는 결정한 줄 안다.
    """

    def __init__(self, message: str, *, conflict: bool = False) -> None:
        super().__init__(message)
        #: 요청 자체가 틀렸나(422) vs 지금 상태에서 받을 수 없나(409)
        self.conflict = conflict


class DecisionIn(BaseModel):
    """`POST /master/runs/{request_id}/decision` 요청 본문.

    사이클(매입 · 판매) 칸은 일부러 두지 않는다. 매입 어휘로 검사할지 판매 어휘로 검사할지는
    실행 이력 행의 `cycle` 이 정한다. 본문에 그 칸이 있으면 매입 실행에 `SALES` 를 실어
    `SL1_PRESENTED` 어휘로 검사받을 수 있고, 그러면 승인 게이트를 부르는 쪽이 고르게 된다.

    주의: `scenario_label` 은 칸 이름과 값이 어긋난다. 판매 후보에는 label 이 없어 이 칸에
    `scenario_id` 를 싣는다. 같은 안을 가리키는 이름이 둘이 되지 않도록 새 칸을 만들지
    않았다.
    """

    model_config = {"extra": "forbid"}

    decision: Decision
    scenario_label: str | None = Field(
        default=None,
        description=(
            "APPROVE 일 때 필수. 그 실행이 실제로 내놓은 안을 가리킨다. "
            "매입은 `scenarios[].label`, 판매는 `candidates[].scenario.scenario_id` 다 "
            "— 판매 후보에는 label 이 없어 이 칸에 scenario_id 를 싣는다."
        ),
    )
    condition_text: str | None = Field(
        default=None,
        min_length=1,
        description="REQUEST_CHANGE 일 때 필수. 조건 없는 재요청은 그냥 거절이다.",
    )
    decided_by: str = Field(
        min_length=1,
        description="승인자. 승인자가 없는 승인은 승인이 아니다.",
    )
    history_run_id: str | None = Field(
        default=None,
        description=(
            "화면이 보고 있던 실행의 이력 행 id. 주면 그 실행으로 검사하고 그것을 "
            "가리켜 기록한다. 안 주면 서버가 최신 실행을 고르는데, 그 사이 재실행이 "
            "있었으면 사람이 본 것과 다른 안이 승인된 것으로 남는다."
        ),
    )
    note: str | None = None

    @model_validator(mode="after")
    def _shape_matches_decision(self) -> DecisionIn:
        """DB CHECK 과 같은 규칙을 입구에서도 건다.

        두 곳에 두는 것이 중복이 아니다 — DB 는 다른 경로로 들어온 행도 막고, 여기는
        사용자에게 이유를 돌려준다. 뒤에서 터지면 500 이 된다.
        """
        if self.decision == "APPROVE" and not self.scenario_label:
            raise ValueError("APPROVE 에는 고른 안(scenario_label)이 있어야 한다.")
        if self.decision != "APPROVE" and self.scenario_label:
            raise ValueError(
                f"{self.decision} 에는 scenario_label 을 넣지 않는다 — "
                "무엇을 거절했는지가 두 가지로 읽힌다."
            )
        if self.decision == "REQUEST_CHANGE" and not self.condition_text:
            raise ValueError("REQUEST_CHANGE 에는 조건(condition_text)이 있어야 한다.")
        return self


class ArrivalLegOut(BaseModel):
    """입고 예정 1회분. 회차(`seq`)마다 품목 · 수량(kg) · 매입일 · 도착일을 함께 싣는다."""

    item: str
    qty_kg: float
    arrival_date: date
    purchase_date: date
    seq: int


class CommitmentOut(BaseModel):
    """승인이 만든 확정 입고 약정. 물류의 미래 창고 점유 입력이 된다.

    `buildable=False` 는 `None` 과 다르다. 승인이 아니어서 약정이 없으면 이 객체 자체가
    `None` 이고, 승인했는데 약정을 못 만들었으면 `buildable=False` 와 `reason` 이 실린다.
    후자를 비워 두면 물류가 "입고 예정이 없다" 로 읽는다.
    """

    buildable: bool = True
    reason: str | None = Field(
        default=None, description="못 만든 이유. `buildable=False` 일 때만 찬다."
    )

    approval_id: str | None = None
    item: str | None = None
    scenario_label: str | None = None
    total_qty_kg: float | None = None
    total_amount_krw: float | None = None
    inbound_lead_days: float | None = None
    first_arrival: date | None = None
    arrival_schedule: list[ArrivalLegOut] = Field(default_factory=list)
    notes: list[str] = Field(
        default_factory=list,
        description="약정은 섰으나 일정을 못 만든 사유 등 — 빈 일정을 설명한다.",
    )

    @classmethod
    def of(cls, commitment: ApprovedCommitment) -> CommitmentOut:
        return cls(
            approval_id=commitment.approval_id,
            item=commitment.item,
            scenario_label=commitment.scenario_label,
            total_qty_kg=commitment.total_qty_kg,
            total_amount_krw=commitment.total_amount_krw,
            inbound_lead_days=commitment.inbound_lead_days,
            first_arrival=commitment.first_arrival,
            arrival_schedule=[
                ArrivalLegOut(
                    item=leg.item,
                    qty_kg=leg.qty_kg,
                    arrival_date=leg.arrival_date,
                    purchase_date=leg.purchase_date,
                    seq=leg.seq,
                )
                for leg in commitment.arrival_schedule
            ],
            notes=list(commitment.notes),
        )


class DecisionOut(BaseModel):
    """적재된 결정 1건."""

    decision_id: UUID
    request_id: str
    decision_seq: int
    decision: Decision
    scenario_label: str | None = None
    condition_text: str | None = None
    decided_by: str
    follow_up_request_id: str | None = None
    end_code_at_decision: str

    #: 최종 승인 시점 재검증이 돈 새 실행의 업무 키. DB 컬럼도 같은 이름이다.
    #:
    #: `follow_up_request_id` 와 다른 사건이다. 저쪽은 `REQUEST_CHANGE` 가 낳은 재요청이라
    #:   결정 뒤에 사람이 다시 돌린 것이고, 이쪽은 결정을 적기 전에 서버가 돌린 것이다.
    #:   게다가 재검증은 새 `as_of` · 새 `request_id` 로 도는 반면 승인 자체는 원
    #:   실행(`history_run_id`)에 달려 있다 — 한 칸에 담으면 그 값이 어느 쪽인지 아무도
    #:   모른다.
    revalidation_request_id: str | None = None

    #: 그 재검증의 결과. `None` 은 "재검증을 하지 않았다" 이고 실패가 아니다 — 승인이
    #: 아닌 결정과, 재검증이 생기기 전(2026-09-07 이전)에 적힌 결정이 그렇다.
    revalidation_outcome: RevalidationOutcome | None = None
    #: 이 결정이 가리키는 실행 이력 행. DB 컬럼은 `master_decisions.run_id` 다.
    #: `None` 은 "실행이 없다"가 아니라 "어느 실행인지 기록되지 않았다" 이다 —
    #: 2026-08-30 이전 결정이 그렇다.
    history_run_id: str | None = None
    note: str | None = None
    created_at: datetime

    is_current: bool = Field(
        default=False,
        description="최신 결정인가. 번복이 있으면 이전 것은 False — 지우지 않고 접는다.",
    )

    #: 승인이 만든 확정 입고 약정 (H1). 승인이 아니면 `None` 이고, 승인인데 못
    #: 만들었으면 `buildable=False` 와 사유가 실린다 — 둘을 섞지 않는다.
    #: 적재 대상이 아니라 응답 전용이라 이력 조회에는 안 실린다.
    commitment: CommitmentOut | None = None

    #: 그 약정이 재무·물류 장부를 실제로 바꾼 결과 (C 형태 ⑦). 약정이 서지 않았으면
    #: `None` 이고, 섰는데 전이 파트가 등록돼 있지 않으면 `NOT_APPLIED` 와 빠진 파트가
    #: 실린다 — `None` 과 섞지 않는다. 반영하려다 실패한 것은 `FAILED` 다.
    #: 이것도 적재 대상이 아니라 응답 전용이라 이력 조회에는 안 실린다.
    transition: TransitionOut | None = None

    #: 판매 승인이 판매 원장에 남긴 결과 (`sales` · `sale_items` · `CONFIRMED`).
    #:
    #: `commitment` · `transition` 과 섞지 않는다. 저 둘은 매입 승인의 효력이고 이것은
    #:   판매 승인의 효력이다 — 한 칸에 담으면 어느 사이클의 승인이었는지가 값의
    #:   모양으로만 읽힌다.
    #:
    #: 승인이 아니거나 매입 실행이면 `None` 이고, 승인인데 확정 못 했으면 `BLOCKED` 와
    #:   사유가 실린다 — 둘을 섞지 않는다 (§1.2-10).
    #:
    #: 응답 전용이라 이력 조회에는 안 실린다.
    sale: SaleConfirmationOut | None = None
