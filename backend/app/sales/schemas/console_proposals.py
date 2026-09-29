"""그날 만든 판매안 응답 — `readmodel/console_proposals.py` 가 채운다.

★ 2026-09-29 BL-013: `sales/console_proposals.py` 에서 옮겼다. 안의 자리를 가르는 판정은
  `domain/console_proposals.py` 다.
"""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

#: 한 안이 **사용자 앞에서 어떤 자리에 서 있는가.**
#:
#: ```text
#: PRESENTABLE       판정이 났고 통과했다        → 승인으로 갈 수 있다
#: REVIEW_REQUIRED   판정이 났고 «확인 필요» 다  → 사람이 봐야 한다
#: REJECTED          판정이 났고 «안 된다» 다    → 조건을 바꿔야 한다
#: UNRESOLVED        판정 자체가 안 났다          → 없는 자료를 채워야 한다
#: ```
#:
#: 🔴 **`UNRESOLVED` 와 `REJECTED` 를 같은 줄로 보여주면 화면이 거짓말을 한다.**
#:    탈락은 «다 봤는데 안 된다» 이고 미판정은 «아직 안 봤다» 다. 둘을 섞으면 사용자는
#:    자료를 채워야 할 날에 조건을 바꾸고, 같은 자리에서 또 막힌다. 마스터가 종료 코드에
#:    `SL3_ALL_REJECTED` 와 `SL6_VALIDATION_UNRESOLVED` 를 따로 둔 것과 같은 이유다.
SalesPresentationState = Literal[
    "PRESENTABLE", "REVIEW_REQUIRED", "REJECTED", "UNRESOLVED"
]

#: 그날 판매 화면 전체가 어떤 상태인가. **후보가 없는 것과 판정이 없는 것은 다르다.**
#:
#: ```text
#: EMPTY         안 자체를 못 만들었다
#: UNRESOLVED    안은 있는데 권위 검증이 안 끝났다
#: REJECTED      판정이 다 났고 통과가 하나도 없다
#: PRESENTABLE   통과한 안이 하나라도 있다
#: ```
SalesProposalsState = Literal["EMPTY", "UNRESOLVED", "REJECTED", "PRESENTABLE"]


class ConsoleSalesStrategy(BaseModel):
    """그 요청의 **전략이 어떻게 섰는가.** 저장된 라벨만 담는다.

    🔴 **HTTP 원문도 provider 응답 본문도 담지 않는다.** 실패 사유는 저장된 어휘
       (`HTTP_400` · `HTTP_429` · `PROVIDER_UNREACHABLE` · `CONTRACT_VIOLATION`)뿐이다 —
       원문을 화면까지 내보내면 키나 내부 주소가 사용자 브라우저에 실린다.
    """

    model_config = ConfigDict(extra="forbid")

    #: 무엇이 자세를 골랐나. `LLM` 또는 `TEMPLATE_FALLBACK`.
    source: str | None
    #: 모델에 무슨 일이 있었나. `SUCCESS` · `SKIPPED_TEMPLATE` · `FALLBACK` · `DISABLED`.
    llm_status: str | None
    #: 🔴 **왜 실패했나.** 성공했거나 안 켠 날은 `None` 이다.
    llm_failure_reason: str | None
    #: 모델이 고른 자세를 사실이 내린 자리.
    clamped_reason_codes: list[str]
    #: 자세는 갈렸는데 숫자가 수렴했는가.
    collapsed: bool
    collapse_reason_codes: list[str]


class ConsoleSalesProposal(BaseModel):
    """판매안 하나. **저장된 값만 담는다.**"""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    #: 사용자가 보고 선택한 마스터 판매 실행. 승인 요청은 이 값을 반드시 함께 보낸다.
    #: 없으면 최신 실행을 고르는 경합이 생기므로 화면은 확정을 열지 않는다.
    history_run_id: str | None
    scenario_id: str
    #: 안의 성격. `CONSERVATIVE` · `BALANCED` · `AGGRESSIVE` 같은 저장값 그대로다.
    scenario_type: str | None
    objective: str | None
    item: str | None
    partner_id: str | None
    quantity_kg: Decimal | None
    unit_price_krw: Decimal | None
    #: 🔴 판매가 적어 보낸 매출액이다. 화면이 수량×단가로 다시 만들지 않는다.
    reported_sales_amount_krw: Decimal | None
    payment_days: int | None
    delivery_date: date | None
    #: 판매가 스스로 매긴 상태. 재무 판정과 다른 축이다.
    status: str | None
    rationale: list[str]
    risks: list[str]
    uncertainties: list[str]
    #: 재무가 같은 요청·같은 안에 남긴 판정. 없으면 `None` 이고 0 이나 통과가 아니다.
    finance_verdict: str | None
    finance_status: str | None
    #: 🔴 **판정을 가른 규칙의 사유다.** 통과 사유는 담지 않는다 — 최상위
    #:   `reason_codes` 에는 통과 사유까지 섞여 있어 그대로 쓰면 «거절 사유» 가 아니다.
    finance_reason_codes: list[str]
    #: 판정을 뒷받침한 재무 숫자. 없으면 `None` 이고 화면이 0 으로 채우지 않는다.
    contribution_margin_krw: Decimal | None
    contribution_margin_rate: Decimal | None
    #: 🔴 여신 칸은 **재무가 센 값**을 옮긴다. 판매·화면이 한도에서 미수를 빼지 않는다.
    current_partner_ar_krw: Decimal | None
    available_credit_krw: Decimal | None
    projected_partner_ar_krw: Decimal | None
    credit_limit_krw: Decimal | None
    #: 판매 전에 먼저 받아야 하는 미수금. **0 은 «더 받을 필요 없음» 이고 `None` 은 «모름» 이다.**
    required_collection_before_sale_krw: Decimal | None
    credit_utilization_rate: Decimal | None
    #: 계약상 결제 예정일 기준의 예상. 입금 보장일이 아니다.
    expected_credit_recovery_date: date | None
    #: 판매가 «이건 아직 못 받았다» 고 적어 둔 검증. 재무 판정이 없는 이유가 여기 있다.
    missing_capabilities: list[str]
    #: 이 안이 어디에 기대어 섰는지. 판매가 회신에 실은 참조를 그대로 나른다.
    evidence_refs: list[str]
    source_ref: str | None
    #: 원가 기준. 마진을 재무가 세는 근거이고, 없으면 재무가 판정을 닫는다.
    cost_basis_amount_krw: Decimal | None
    cost_basis_quantity_kg: Decimal | None
    cost_basis_method: str | None
    cost_basis_refs: list[str]
    #: 이 안이 기댄 물량. 확정분과 조건부분을 가른다.
    confirmed_quantity_kg: Decimal | None
    conditional_quantity_kg: Decimal | None
    additional_supply_required: bool | None
    ml_support_used: bool | None
    #: 판매가 추천으로 표시한 안인지. 저장된 `recommended_scenario_id` 와 같을 때만 참이다.
    recommended: bool
    #: 추천한 안에 판매가 **저장해 둔** 추천 이유. 없으면 `None` 이다 — 화면이 지어내지 않는다.
    recommendation_reason: str | None = None
    #: 이 안이 실제 판매로 확정됐는지. 같은 실행의 `sales` 행이 있을 때만 그 주문 상태다
    #: (`CONFIRMED` · `DELIVERED`). **없으면 `None` 이고 «선택» 이나 «추천» 과 다르다.**
    sale_status: str | None = None
    #: 🔴 **이 안이 사용자 앞에서 서는 자리.** 판매 상태와 재무 판정을 함께 읽어 정한다.
    presentation_state: SalesPresentationState = "UNRESOLVED"
    #: 승인으로 보낼 수 없는가. **`PRESENTABLE` 이 아닌 모든 안이 참이다.**
    #:
    #: ★ 화면이 후보를 **보여주는 것**과 **확정으로 보내는 것**은 다른 사실이다. 미판정
    #:   후보도 보여줄 수 있지만 확정 경계는 그대로 닫혀 있어야 한다.
    approval_blocked: bool = True
    #: 판정이 왜 안 났는가. `presentation_state` 가 `UNRESOLVED` 일 때만 채운다.
    unresolved_reason_codes: list[str] = []
    #: 그 요청의 전략이 어떻게 섰는가. 같은 요청의 모든 안이 같은 값을 본다.
    strategy: ConsoleSalesStrategy | None = None


class ConsoleSalesProposalsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sim_run_id: str
    as_of: date
    #: 그날 판매가 실제로 돈 요청 수. 안이 0개여도 돈 것은 돈 것이다.
    request_count: int
    #: 🔴 **팔 물량이 0인 안은 목록에서 뺀다.** 그런 안은 재무가 검토할 것도 없어
    #:   판정이 영원히 안 붙고, 화면에서는 «재무 검토 전» 으로 남아 실제로 밀린 안처럼
    #:   보인다. 지우는 것이 아니라 **몇 건을 뺐는지 숫자로 남긴다.**
    hidden_zero_quantity: int
    #: 🔴 **«후보가 없다» 와 «판정이 없다» 를 한 문구로 합치지 않는다.**
    state: SalesProposalsState = "EMPTY"
    presentable_count: int = 0
    unresolved_count: int = 0
    rejected_count: int = 0
    review_required_count: int = 0
    rows: list[ConsoleSalesProposal]
