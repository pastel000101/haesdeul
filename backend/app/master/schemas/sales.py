"""판매 실행 요청 · 응답 모델과 판매 종료 코드 어휘.

★ 2026-09-30 재구성 BL-018: `master/sales_flow.py` 에서 옮겼다 — `SalesEndCode`.
★ 2026-09-30 재구성 BL-018: `master/schemas.py` 에서 옮겼다 — `SalesBusinessMode`,
  `SalesRunRequest`, `SalesCandidateOut`, `SalesRunResponse`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.contracts.core import ITEMS
from app.contracts.envelope import Trigger
from app.master.schemas.day_gate import DayGate
from app.master.schemas.run_response import AdjustmentOut, BlockedAgentOut, EvidenceOut, StepOut

SalesBusinessMode = Literal[
    "CONTRACT_FULFILLMENT",
    "CONTRACT_PROPOSAL_NEW",
    "CONTRACT_PROPOSAL_RENEWAL",
    "SPOT_SALES",
]
"""판매 사이클의 영업 모드. **어휘의 주인은 판매다** (`app/sales/schemas/proposal.py`).

🔴 **그런데 마스터가 `app.sales.schemas` 를 import 하지 않는다.** `Capability` 때와
  같은 이유다 — 조정자가 부서 스키마에 런타임으로 묶이면 부서가 자기 모델을 고치는 날
  마스터 API 가 같이 흔들린다.

★ **대신 테스트가 양쪽을 대조한다** (`tests/master/test_sales_entrypoint.py`).
  갈려도 런타임에는 아무 소리가 안 나기 때문이다 — 판매가 모드를 하나 늘리면
  마스터 문 앞에서 422 가 나고, 그것은 *"그런 모드는 없다"* 로 읽힌다.
"""


# ---------------------------------------------------------------------------
# 판매 사이클 — **매입과 대칭으로 두되 한 벌로 묶지 않는다** (설계 §1 · 2026-09-07)
#
# 🔴 두 사이클은 응답 모델도 종료 코드도 다르다. 공유하는 것은 **판정(개장 Gate)과
#   순서**이지 응답이 아니다. 억지로 묶으면 판매 종료 코드가 매입 어휘로 새거나 그
#   반대가 된다 — `SL2_NO_CANDIDATE` 를 `E2_HELD` 로 적는 날이 온다.
# ---------------------------------------------------------------------------


class SalesRunRequest(BaseModel):
    """사용자가 눌러서 시작하는 판매 요청 (설계 §2).

    ★ `ProcurementRunRequest` 와 대칭이되 **판매에만 있는 것이 셋**이다 —
      `business_mode` · `partner_id` · `user_request`.

    🔴 **`has_unmet_obligation` 은 싣지 않는다.** 그것은 매입 `E5` 판정 전용이고
      **판매가 준 사실을 매입이 쓰는 값**이다. 판매 요청에 되돌려 실으면 순환이다 —
      판매가 자기가 준 사실을 자기 입력으로 다시 받는다.
    """

    model_config = {"extra": "forbid"}

    as_of: date
    policy_version: str = Field(min_length=1)
    trigger: Trigger = "USER_REQUEST"
    request_id: str | None = Field(
        default=None,
        description="주지 않으면 마스터가 만든다. 같은 날 재실행을 구분하려면 직접 준다.",
    )

    #: 🔴 **25 다. 매입 12 가 아니다** (설계 §3).
    #:
    #:   골격에 `SALES_BUDGET = 25` 가 있지만 **요청이 12 를 들고 오면 그 값이 이긴다.**
    #:   매입 스키마를 복사해 오면 실제로 그렇게 되고, 소진은 `SL5_BUDGET_EXHAUSTED`
    #:   로 조용히 남는다 — 판단이 안 끝난 날이 늘어나는데 아무 오류도 안 난다.
    #:
    #:   ```text
    #:   후보 3 · 되먹임 2회 최악 경우
    #:     inventory PRE_SALES            1
    #:     sales GENERATE_SALES_PROPOSAL  3   (최초 1 + 되먹임 2)
    #:     finance SALES_VALIDATION       9   (후보 3 × 회차 3)
    #:     purchase SUPPLY_CAPACITY_QUERY 9   (품목 3 × 회차 3)
    #:                                   22
    #:   +2  후보 범위·날짜가 바뀌어 물류를 다시 부르는 경우 (판매 v1.7 §5)
    #:   +1  S-2 (ERROR 1회 재시도) 여유
    #:                                   25
    #:   ```
    #:
    #:   ★★ **매입 줄은 「후보 수」가 아니라 「품목 수」다** (2026-09-10 · 라우팅 개방).
    #:     호출을 품목으로 묶으므로 후보 셋이 다 배추면 회차당 1 이다. 후보가 셋이면
    #:     품목도 최대 셋이라 상한만 같은 수가 된다.
    #:
    #:   ★ 산식의 주인은 `sales_flow.SALES_BUDGET` docstring 이다. 여기 적힌 것은
    #:     **왜 12 가 아닌지**를 고치는 사람이 바로 보게 하려는 사본이고, 값 자체는
    #:     기본값 하나뿐이다.
    budget: int = Field(
        default=25,
        ge=1,
        le=50,
        description=(
            "에이전트 호출 상한 (§1.2-12). 판매 기본값은 25 — 후보 3 · 되먹임 2회면 "
            "물류 1 + 판매 3 + 재무 9 + 매입 9 = 22 이고, 물류 재조회 2 · S-2 재시도 1 을 "
            "더해 25. 매입 기본값 12 를 그대로 쓰면 골격의 SALES_BUDGET 을 요청이 이긴다."
        ),
    )

    item: str | None = Field(
        default=None,
        description=(
            "이번 실행이 다루는 품목 (배추·무·양파). 주지 않으면 마스터가 싣지 않고, "
            "판매가 missing_data 로 그 사실을 낸다."
        ),
    )

    # ── 판매에만 있는 셋 ────────────────────────────────────────────
    business_mode: SalesBusinessMode = Field(
        description=(
            "무슨 판매인가 — 계약 이행 · 신규 제안 · 갱신 제안 · 현물. "
            "어휘의 주인은 판매이고 마스터는 자기 Literal 로 선언한다 (SalesBusinessMode)."
        )
    )
    partner_id: str | None = Field(
        default=None,
        description=(
            "거래처. 계약 이행·갱신에서는 사실상 필수지만 **마스터가 강제하지 않는다** — "
            "무엇이 필요한지는 판매가 정한다 (§3.2.2)."
        ),
    )
    user_request: str | None = Field(
        default=None,
        description=(
            "사용자가 말한 것 **그대로**. 마스터가 숫자로 해석해 제약에 꽂지 않는다 — "
            "해석은 판매가 한다 (매입 `prior_feedback` 과 같은 자리)."
        ),
    )

    #: 🔴 **자유 문장으로는 수량을 못 나른다** (실측 2026-09-07).
    #:
    #:   판매는 `raw_text` 를 해석해 수량을 뽑지 않는다. 그래서 `user_request` 만
    #:   보내면 `PROPOSAL_QUANTITY_REQUIRED` 로 `RUNTIME_NOT_READY` 가 돌아온다 —
    #:   **판매가 실제로 요구한 칸**이라 여기에 자리를 만든다.
    #:
    #: 🔴 **나머지 구조화된 칸은 지금 안 넣는다.** `SalesUserRequest` 에는
    #:   `preferred_contract_term_days` 가 더 있지만 **요구한 caller 가 아직 없다.**
    #:   없는 필요를 API 표면에 미리 만들면 화면이 안 쓰는 칸을 채우기 시작하고,
    #:   그 값이 어디서 왔는지 아무도 모른 채 제안에 실린다.
    #:
    #: ★ **못 나르는 칸이 조용히 잊히지는 않는다** —
    #:   `tests/master/test_sales_user_request_fields.py` 가 판매 모델을 읽어
    #:   나르는 칸과 안 나르는 칸을 대조한다. 판매가 칸을 더하면 그 검사가
    #:   *"이건 어느 쪽이냐"* 고 묻는다.
    requested_quantity_kg: Decimal | None = Field(
        default=None,
        ge=0,
        description=(
            "사용자가 말한 요청 수량 (kg). 판매 `SalesUserRequest.requested_quantity_kg` "
            "로 그대로 나간다 — 마스터는 단위도 값도 고치지 않는다."
        ),
    )

    #: 🔴 **상업조건 둘이 여기로 온다** (2026-09-08 계약 · 실측).
    #:
    #:   `delivery_date` · `payment_days` 가 없는 후보는 **승인해도 확정할 수 없다**
    #:   (`sales_approval.REQUIRED_COMMERCIAL_TERMS`). 그래서 후보 판정이 그 둘을
    #:   필수로 잡았고, 그 순간 *"요구한 caller 가 없다"* 가 깨졌다 — **요구한 caller
    #:   가 승인 경로다.**
    #:
    #:   ```text
    #:   판매 proposal.py `_baseline`   delivery = user.preferred_delivery_date
    #:                                  payment  = user.preferred_payment_days
    #:   ```
    #:
    #:   실측(2026-09-08)에서 판매 후보 3안이 전부 `delivery_date=None` ·
    #:   `payment_days=None` 이었던 이유가 이것이다 — **마스터가 안 실어 보냈다.**
    #:   판매가 값을 안 만든 것이 아니라 출처가 비어 있었다.
    #:
    #: ★ **마스터가 값을 지어내지 않는다.** 안 주면 안 싣고, 그러면 후보가 제시되지
    #:   않으며 그 사유는 *"납품일이 없다"* 로 화면에 나간다. `as_of + N` 같은
    #:   기본값을 두면 그 `N` 이 곧 업무 규칙이 된다.
    preferred_delivery_date: date | None = Field(
        default=None,
        description=(
            "사용자가 말한 납품 희망일. 판매 `SalesUserRequest.preferred_delivery_date` "
            "로 그대로 나가고, 승인되면 `sales.sale_date` 의 출처가 된다."
        ),
    )
    preferred_payment_days: int | None = Field(
        default=None,
        ge=0,
        description=(
            "사용자가 말한 수금 유예일. 판매 `SalesUserRequest.preferred_payment_days` "
            "로 그대로 나가고, 승인되면 수금 기일(`sale_date + payment_days`)의 출처가 된다."
        ),
    )

    #: 🔴 **재무가 요구한 셋이 여기로 온다** (2026-09-11 · 걷기 실측).
    #:
    #:   ```text
    #:   재무 SALES_VALIDATION  status "INPUT_INCOMPLETE"
    #:   missing_fields  partner_id · unit_price_krw · reported_sales_amount_krw
    #:                   payment_terms_type · source_ref
    #:   ```
    #:
    #:   `partner_id` 는 이미 있었고 `reported_sales_amount_krw` 는 **판매가 수량 ×
    #:   단가로 자기 안에서 세는 파생**이다. 마스터가 실을 수 있는 자리는 나머지
    #:   셋이고, 자동 걷기가 그 셋을 안 실어서 재무가 판정을 못 냈다.
    #:
    #: ★ **마스터가 값을 지어내지 않는다** (`preferred_delivery_date` 와 같은 규율).
    #:   자동 걷기의 값은 **실행 규칙 파일**에서 오고 (`backfill.SalesTermsRule`),
    #:   규칙이 없으면 셋 다 `None` 이라 종전과 똑같이 안 실린다.
    preferred_unit_price_krw: Decimal | None = Field(
        default=None,
        ge=0,
        description=(
            "희망 단가 (원/kg). 판매 `SalesUserRequest.preferred_unit_price_krw` 로 "
            "그대로 나간다 — 마스터는 시세를 계산하지 않는다."
        ),
    )

    #: ⚠️ **`SalesBusinessMode` 처럼 Literal 로 선언하지 않는다** (2026-09-11).
    #:
    #:   영업 모드는 **마스터가 고른다** (`scheduler.WALK_BUSINESS_MODE`) — 고르는
    #:   쪽이 아는 이름을 제 어휘로 선언하는 것이 맞다. 지급조건 종류는 **마스터가
    #:   고르지 않는다.** 규칙 파일이 말한 것을 나르기만 하므로, 여기에 Literal 을
    #:   두면 **업무의 값이 코드에 박히고** 판매가 어휘를 늘리는 날 마스터가 문
    #:   앞에서 먼저 거절한다. 아는 이름인지는 판매 문(`SalesUserRequest`)이 판정한다.
    preferred_payment_terms_type: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "지급조건 종류. 판매 `SalesUserRequest.preferred_payment_terms_type` 로 "
            "그대로 나가고, 어휘의 주인은 판매다 — 마스터는 값을 검사하지 않는다."
        ),
    )

    #: 🔴 **이 조건을 누가 정했나.** 사람이 화면에 친 문장에는 되짚을 행이 없어
    #:   종전에는 이 칸을 안 날랐다. **자동 걷기는 다르다** — 조건이 실행 규칙에서
    #:   왔고 그 규칙은 `sim_runs.config_json` 이라는 되짚을 행에 있다.
    #:
    #: ⚠️ **사람이 말한 것처럼 보이면 안 된다.** 값은 `sales_terms.rules_source_ref`
    #:   가 짓고, 실행 id 와 **단가를 어느 쪽에서 가져왔는지**를 같이 적는다.
    source_ref: str | None = Field(
        default=None,
        description=(
            "이 조건의 권위 있는 출처 ref. 판매 `SalesUserRequest.source_ref` 로 그대로 "
            "나간다 — 되짚을 행이 있을 때만 채운다."
        ),
    )

    allow_additional_sourcing: bool = Field(
        default=False,
        description=(
            "재고 부족 시 추가매입 가능량 검토에 대한 사용자 동의. 기본은 false이며 "
            "실제 매입 확정 권한은 아니다."
        ),
    )
    #: 🔴 **매입과 같은 칸이다** (`ProcurementRunRequest.sim_run_id` · `#531` 후속).
    #:
    #:   판매도 `ExecutionContext` 에 축을 실어야 한다 — `_procurement_boundary` 가
    #:   그 값으로 그날 매입 경계를 읽고, `persistence.record_sales` 가 그 값을
    #:   판단 행에 적는다. 여기만 상수로 남기면 같은 날 매입 행과 판매 행이 **서로
    #:   다른 실행에 앉는다.**
    #:
    #: ★ **기본값이 있는 이유도 매입과 같다** — 라우터·화면은 이 칸을 안 준다.
    sim_run_id: str | None = Field(
        default=None,
        description=(
            "어느 시뮬레이션 실행의 판단인가. 주지 않으면 마스터가 번인 상수를 쓴다 — "
            "매입 `ProcurementRunRequest.sim_run_id` 와 같은 칸이다."
        ),
    )

    @field_validator("item")
    @classmethod
    def _item_is_in_the_contract(cls, value: str | None) -> str | None:
        """계약 밖 품목을 문 앞에서 거른다 — **매입과 같은 규칙이다.**

        ★ `ProcurementRunRequest._item_is_in_the_contract` 와 같은 것을 판매에서도
          한다. 두 사이클이 같은 3품목 계약을 쓰므로 문 앞 판정이 갈리면 안 된다.

        ★ **`None` 은 통과시킨다.** 품목을 안 준 것과 없는 품목을 준 것은 다르다.
        """
        if value is not None and value not in ITEMS:
            raise ValueError(f"지원하지 않는 품목입니다: {value}. 가능: {', '.join(ITEMS)}")
        return value


class SalesCandidateOut(BaseModel):
    """판매 후보 하나와 그 판정 — **골격 `CandidateVerdict` 를 그대로 옮긴다.**

    🔴 **`passed` · `unvalidated` · `detail` 을 화면이 다시 계산하면 안 된다.**
      통과 판정은 허용목록(`PASSING_VERDICTS`)으로 정해지는데, 그 목록은 봉투 어휘가
      늘 때 같이 는다. 화면이 *"reject 가 아니면 통과"* 로 다시 세면 어휘가 는 날
      새 값이 통과 쪽으로 샌다 (#173 이 고친 것과 같은 실수).
      **주인은 `CandidateVerdict` 이고 여기 실린 것은 그 답이다.**
    """

    #: 판매가 낸 후보 그대로. **마스터는 고르지도 재계산하지도 않는다** (§3.2.2).
    scenario: dict[str, Any]

    #: capability → 그 검증의 회신. 키는 판매가 요구한 이름 그대로다.
    validations: dict[str, dict[str, Any]] = {}

    #: 🔴 **부를 대상이 없어 못 물어본 요구.** 비어 있지 않으면 통과로 치지 않는다.
    unroutable: list[str] = []

    #: 🔴 **비어 있는 상업조건이 어디서 끊겼나.** 비어 있지 않으면 통과로 치지 않는다 —
    #: 값이 없는 안은 사용자가 골라도 확정할 수 없다 (2026-09-08 계약).
    #:
    #: ```text
    #: REQUEST_MISSING_<FIELD>    부르는 쪽이 안 보냈다  → 고칠 사람: 화면 · 걷기 · API 호출자
    #: TERMS_UNRESOLVED_<FIELD>   정말 조건이 없다       → 고칠 사람: 판매 · 계약
    #: ```
    #:
    #: ★ **칸 이름은 접두를 벗기면 나온다** (`sales_approval.term_of_origin`). 화면이
    #:   문자열을 직접 자르지 않는다 — 접두를 바꾸는 날 조용히 틀린 이름이 뜬다.
    #:
    #: ★ **주인은 `CandidateVerdict.missing_terms` 다.** 여기서 원인을 다시 세지 않는다 —
    #:   `user_request` 도 `business_mode` 도 이 응답에는 없다.
    #:
    #: ⚠️ **`validations` 와 다른 칸이다.** 부서 판정에 섞으면 화면이 *"재무가 반려"*
    #:   로 읽고 사람이 재무를 보러 간다 — 봐야 할 곳은 판매다.
    missing_terms: list[str] = []

    passed: bool
    #: 요구한 검증이 하나도 없었다 — **통과로 나가지만 아무도 안 본 안이다.**
    unvalidated: bool
    #: 왜 탈락했나. 부서가 쓴 문장 그대로이고 마스터가 요약하지 않는다.
    detail: str


class SalesRunResponse(BaseModel):
    """판매 Flow 한 번의 결과.

    ★ **매입 응답과 닮았지만 담는 것이 다르다.** 매입은 *"시나리오 배열 + 부서별
      판정"* 이고 판매는 **후보마다 자기 판정을 들고 있다** — 부분 통과가 정상이라
      부서 축으로 접으면 어느 후보가 왜 떨어졌는지가 사라진다 (C-1).
    """

    request_id: str
    as_of: date

    #: 개장 관문 결과. `None` 은 **관문을 안 물었다**는 뜻이다.
    #:
    #: 🔴 **판매에는 실행일 관문이 없다** — 주말에도 판다 (설계 §1). 그래서 매입과
    #:   달리 이 블록이 `BLOCKED` 인 것 말고 *"안 도는 날"* 이 없다.
    day_gate: DayGate | None = None

    #: 이 실행이 이력에 남은 행의 id (`master_agent_runs.run_id`). 적재 실패면 `None`.
    history_run_id: str | None = None

    end_code: SalesEndCode
    reason: str

    candidates: list[SalesCandidateOut] = Field(
        default=[],
        description=(
            "통과·탈락을 **한 칸에** 담는다. 가르는 것은 각 후보의 `passed` 다 — "
            "두 칸으로 두면 같은 후보가 양쪽에 들어가는 날을 아무도 못 막는다. "
            "SL1 에서도 탈락 후보가 비어 있지 않을 수 있다 (사유를 동봉해 함께 낸다)."
        ),
    )

    judgment: dict[str, Any] = Field(
        default={},
        description=(
            "`scenarios` 를 뺀 제안 최상위 — 판매의 situation · business_mode · self_check. "
            "**키를 고르지 않는다** — 화이트리스트로 뽑으면 판매가 판정 필드를 늘릴 때마다 "
            "마스터를 고쳐야 하고, 빠뜨린 키는 커버리지를 감춘 상태가 된다."
        ),
    )

    supply_context: dict[str, Any] = Field(
        default={},
        description=(
            "②에서 받은 초기 물류 컨텍스트. 못 받았으면 비어 있고 사유는 `context_failure` 다."
        ),
    )
    context_failure: BlockedAgentOut | None = Field(
        default=None,
        description=(
            "🔴 물류가 컨텍스트를 못 냈다는 사실. 판매는 밴드가 없어 여기서 멈추지 않지만, "
            "멈추지 않는 것과 없던 일로 하는 것은 다르다 — 후보의 질이 왜 떨어졌는지를 "
            "나중에 읽는 사람이 볼 수 있어야 한다."
        ),
    )

    ml_context_note: str = Field(
        default="",
        description=(
            "🔴 ML 예측을 **못 실은 이유**. 실었으면 비어 있다. "
            "판매는 ML 을 직접 부르지 않고 마스터가 받아 실어 나른다 (판매 v1.7 §11) — "
            "못 실으면 후보가 시장 예측 없이 만들어지므로, 멈추지는 않되 그 사실이 "
            "결론과 함께 보여야 한다. 매입 `mocked_inputs` · `input_sources` 와 같은 자리다."
        ),
    )

    evidences: list[EvidenceOut] = Field(
        default=[],
        description="부서가 낸 근거. **마스터가 고르거나 요약하지 않는다** — 매입과 같은 규율이다.",
    )
    adjustments: list[AdjustmentOut] = Field(
        default=[],
        description="부서가 낸 조정안 표준형. 되먹임에 실린 것과 같은 값이다.",
    )

    feedback_attempts: int = Field(
        default=0,
        description=(
            "실제로 돈 되먹임 회차. **0 이면 되먹임하지 않았다** — 통과 후보가 있었거나 "
            "권위 있는 대안이 없었다."
        ),
    )

    findings: list[str] = Field(
        default=[],
        description=(
            "판매 Critic(B)이 낸 발견. **매입 `findings` 와 같은 칸 이름이고 같은 뜻이다** "
            "— 다시 만들면 달라질 수 있는 것."
        ),
    )
    concerns: list[str] = Field(
        default=[],
        description=(
            "사실이지만 **다시 만들어도 안 고쳐지는** 것 — 부서 계약 위반 · 마스터 배선 "
            "문제 · 검증 Tool 이 돌다 죽은 사실. 사람이 봐야 한다 (§3.4)."
        ),
    )
    skipped_checks: list[str] = Field(
        default=[],
        description=(
            "🔴 검증 Tool 이 **판정하지 못한** 검사와 사유. 판매는 오늘 여기가 대부분이다 "
            "— `inventory_allocations` 가 0행이라 배분·로트 재료가 없다. 비어 있는 "
            "findings 를 '전부 통과' 로 읽지 않게 한다 (§3.7.6)."
        ),
    )
    verification_skipped: bool = Field(
        default=False,
        description="검증 자체가 **안 돌았는가.** 시작조차 못 한 날(SL4)이 참이다.",
    )

    plan: list[StepOut] = []
    plan_signature: list[tuple[str, str, int]] = Field(
        default=[],
        description="누구를 어떤 목적으로 몇 번째로 불렀는가. 같은 입력에 같은 값이어야 한다.",
    )

    report_text: str = Field(
        default="",
        description=(
            "🔴 **마스터가 판매 문장을 짓지 않는다** (설계 §5). 추천 문장과 순위는 판매 "
            "소유이고 마스터는 순위를 재계산하지 않는다 (판매 v1.7 §18). 그래서 "
            "`SL1_PRESENTED` 에서는 **비어 있다** — 그 날의 문장은 판매가 낸 것이 답이다. "
            "Flow 가 접힌 날(SL2~SL5)만 **왜 접혔는지** 한 줄이 들어간다. 업무 판단이 "
            "아니라 실행 사실이다."
        ),
    )


SalesEndCode = Literal[
    "SL1_PRESENTED",
    "SL2_NO_CANDIDATE",
    "SL3_ALL_REJECTED",
    "SL4_NOT_STARTED",
    "SL5_BUDGET_EXHAUSTED",
    "SL6_VALIDATION_UNRESOLVED",
]
"""판매 사이클 종료 코드.

```text
SL1_PRESENTED             통과 후보 ≥ 1 — 사용자에게 제시한다 (탈락안 사유 동봉)
SL2_NO_CANDIDATE          판매가 안을 만들지 못했다 (missing_data / missing_capability)
SL3_ALL_REJECTED          안은 있었으나 전부 탈락 — 되먹임까지 끝났다
SL4_NOT_STARTED           시작하지 못했다 (어댑터 미등록 · mock 입력)
SL5_BUDGET_EXHAUSTED      예산 소진 — 판단이 끝나지 않았다
SL6_VALIDATION_UNRESOLVED 안은 있는데 **판정이 끝나지 않았다** — 탈락이 아니다
```

🔴 **`SL6` 은 `SL3` 의 거짓말을 걷어내려고 생겼다.** 부서가 자료·정책이 없어 판정을
  못 낸 날(`RUNTIME_NOT_READY` · `INPUT_INCOMPLETE` → `skipped`)에도 종료 코드는
  *"전부 탈락"* 이었다. 아무도 탈락시키지 않았는데 탈락이라고 적은 것이라, 읽는
  사람은 **후보를 다시 만들어야 한다**고 읽는다 — 실제로 할 일은 재무 자료를 채우는
  것이고, 후보를 다시 만들어 봐야 같은 자리에서 또 막힌다.

  ```text
  SL3   판정이 났고 그 판정이 "안 된다" 다      → 조건을 바꿔야 한다
  SL6   판정 자체가 안 났다                     → 없는 자료를 채워야 한다
  ```

★ **`SL6` 도 승인 코드가 아니다.** `decision.approve_end_codes` 는 `SL1` 하나만
  승인으로 받는다 — 후보가 살아 있는 것과 승인 가능한 것은 다른 문제다.

★ **`SL5` 와 다르다.** 저쪽은 *"예산이 다해서 더 못 물었다"* 이고 여기는 **물어봤고
  답도 받았는데 그 답이 판정이 아니었다** 이다. 다음에 할 일이 다르다.

🔴 **매입 `EndCode`(E1~E5) 에 값을 더하지 않는다** (D-3 합의). 층이 다르다. 한 어휘에
  두 사이클을 담으면 `E2_HELD` 가 *"매입 보류"* 와 *"판매 보류"* 를 동시에 뜻하게 되고,
  화면과 이력이 어느 사이클의 종료인지를 payload 로 되짚어야 한다.

🔴 **예산 소진을 `SL3` 으로 접지 않는다 — 매입과 일부러 다르다.**

  매입은 `BudgetExhausted` 를 `E3_REJECTED` 로 바꾼다 (`flow.py:378-380`). 그러면
  **"다 봤는데 안 된다"** 와 **"다 못 봤다"** 가 **같은 코드**가 된다.

  매입 쪽을 지금 고칠 일은 아니지만, **새로 만드는 어휘를 같은 모양으로 만들 이유는
  없다.** 판매는 사용자에게 후보를 직접 보여주는 경로라, 못 본 것을 거절로 적으면
  화면이 거짓말을 한다 — 사용자는 *"이 조건으로는 안 된다"* 로 읽고 조건을 바꾸는데,
  실제로는 **판단이 끝나지 않은 것**이라 같은 조건으로 다시 돌리는 것이 맞다.
"""
