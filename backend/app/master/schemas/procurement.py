"""마스터 API 입출력 스키마.

★ 봉투(`envelope.py`)와 API 스키마를 따로 둔다.
  봉투는 **마스터↔에이전트 내부 계약**이고 이건 **외부 노출 계약**이다. 하나로 합치면
  화면 요구가 바뀔 때마다 에이전트 계약이 흔들린다.

★ 2026-09-30 재구성 BL-018: `master/schemas.py` 에서 옮겼다. 역할이 다른 부분은 갈랐다 —
  `schemas/history.py`; `schemas/run_response.py`; `schemas/sales.py`. 무엇이 어디로 갔는지는 설계서
  대응표 `master/` 절.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.contracts.core import ITEMS, EndCode
from app.contracts.envelope import AgentName, Trigger
from app.master.schemas.day_gate import DayGate
from app.master.schemas.run_response import AdjustmentOut, BlockedAgentOut, EvidenceOut, StepOut


class ProcurementRunRequest(BaseModel):
    """사용자 요청 또는 ML 완료 Trigger."""

    model_config = {"extra": "forbid"}

    as_of: date
    policy_version: str = Field(min_length=1)
    trigger: Trigger = "USER_REQUEST"
    request_id: str | None = Field(
        default=None,
        description="주지 않으면 마스터가 만든다. 같은 날 재실행을 구분하려면 직접 준다.",
    )
    has_unmet_obligation: bool = Field(
        default=False,
        description=(
            "판매 Rule 이 주는 사실 — B2B 확정 납품을 채울 수 없는 날이면 참. E5 판정에만 쓴다."
        ),
    )
    budget: int = Field(default=12, ge=1, le=50, description="에이전트 호출 상한 (§1.2-12)")
    item: str | None = Field(
        default=None,
        description=(
            "이번 실행이 다루는 품목 (배추·무·양파). 매입은 품목 하나씩 돈다. "
            "주지 않으면 마스터가 싣지 않고, 매입이 missing_data: ['item'] 을 낸다. "
            "전 품목을 한 번에 도는 것은 미결 — 재무 cap 이 품목 공통이라 배분 규칙이 없다(M-26)."
        ),
    )

    @field_validator("item")
    @classmethod
    def _item_is_in_the_contract(cls, value: str | None) -> str | None:
        """🔴 **계약 밖 품목을 문 앞에서 거른다** (2026-09-03).

        전에는 아무도 안 걸렀다. 피마늘을 계약에서 뺀 뒤 실측하니 요청이 **Critic
        L0 까지 가서** `E-UNKNOWN-ITEM` 으로 죽었다. 막히기는 하는데 늦다 —
        그때까지 매입을 부르고 안을 만들고 세 부서를 다 돈다.

        ★ **`None` 은 통과시킨다.** 품목을 안 준 것과 없는 품목을 준 것은 다르다.
          안 주면 매입이 `missing_data: ["item"]` 으로 그 사실을 낸다 (§1.2-10).

        ★ **`app/ml/router.py:35` 와 같은 모양이다.** ML 이 이미 이렇게 거르고
          있었고, 같은 일을 다른 방식으로 하지 않는다.
        """
        if value is not None and value not in ITEMS:
            raise ValueError(
                f"지원하지 않는 품목입니다: {value}. 가능: {', '.join(ITEMS)}"
            )
        return value

    # ── §3.2.5 의 명시적 예외 — 마스터가 실어 주는 값 ───────────────
    forecast: dict[str, Any] | None = Field(
        default=None,
        description=(
            "ML 예측. ML 은 호출 구조 밖 독립 실행이라 '해당 에이전트에게 요청'이 성립하지 "
            "않는다. `generated_at` 이 as_of 이후면 마스터가 싣지 않는다."
        ),
    )
    confirmed_orders: dict[str, Any] | None = Field(
        default=None,
        description="계약 납품 요구량. 1차 판매는 에이전트가 아니라 마스터 관할 Rule 이다.",
    )
    policy_values: dict[str, Any] | None = Field(
        default=None,
        description="계약 판매단가·마진 방어선 등 정책 테이블 값. 운반 주체는 미결(M-19).",
    )
    prior_feedback: dict[str, Any] | None = Field(
        default=None,
        description=(
            "사람이 조건을 붙여 다시 요청한 경우 그 조건. 매입 `prior_feedback` 계약으로 "
            "**사용자의 말 그대로** 넘어간다 — 마스터가 숫자로 해석하지 않는다. "
            "⚠️ 매입은 현재 이 값을 `is_refeed`·`attempt` 메타로만 쓰고 안을 바꾸지 않는다."
        ),
    )

    #: 🔴 **어느 실행의 판단인가** (`#531` 후속 · 2026-09-10).
    #:
    #:   `walk` 은 `#531` 로 축을 받는데 그 값이 **봉투 앞에서 끊겼다.** 진입점이
    #:   `ExecutionContext(sim_run_id=BURN_IN_SIM_RUN_ID)` 로 상수를 다시 적었고, 그
    #:   아래는 전부 봉투를 읽으므로 **걷기가 축을 줘도 판단 행이 번인으로 앉았다.**
    #:   승인 경로가 그 행에서 축을 읽으니(`decision_service._sim_run_id_of`)
    #:   원장까지 번인으로 돌아왔다.
    #:
    #:   ```text
    #:   요청 sim_run_id → ExecutionContext.sim_run_id → master_agent_runs.sim_run_id
    #:                   → 승인이 그 행에서 읽는다 → purchases.sim_run_id
    #:   ```
    #:
    #: 🔴 **기본값을 없애지 않는다.** 라우터도 화면도 이 칸을 안 주고, 이번 판은
    #:   운영 동작을 안 바꾼다. 대신 **기본값으로 떨어진 사실을 출처표에 적는다** —
    #:   `service._input_sources` 의 `sim_run_id: "DEFAULT:burn_in"` 이 그것이고,
    #:   그 줄이 없으면 *"말 안 하고 번인에 쌓는"* 길이 그대로 남는다.
    #:
    #: ★ **축 이름을 해석하지 않는다.** 마스터는 받은 문자열을 나르기만 한다
    #:   (`sim_run.py` 의 *"이름을 짓되 되읽지 않는다"*).
    sim_run_id: str | None = Field(
        default=None,
        description=(
            "어느 시뮬레이션 실행의 판단인가. 주지 않으면 마스터가 번인 상수를 쓰고 "
            "출처표에 'DEFAULT:burn_in' 으로 적는다 — 값과 출처를 같이 낸다."
        ),
    )


class ProcurementRunResponse(BaseModel):
    request_id: str
    as_of: date

    #: 🔴 **개장 관문 결과** (계약 `260904_마스터_통보_개장Gate_응답모양_next_action`).
    #:
    #:   ```text
    #:   요청 진입 → open_day Gate → execution day Gate → Purchase Flow
    #:   ```
    #:
    #: ★ **화면은 `day_gate.gate` 만 보고 막는다.** `end_code` 는 *"시작 안 했다"* 까지만
    #:   말하고, **왜** 인지는 이 블록이 나른다 — 토요일은 개장을 통과하고 실행일에서
    #:   막히는데, `E4_NOT_STARTED` 하나로는 그 둘이 같아 보인다.
    #:
    #: ⚠️ `None` 은 **관문을 안 물었다**는 뜻이다 (개장 구현이 없는 경로).
    day_gate: DayGate | None = None

    #: 🔴 **이 실행이 이력에 남은 행의 id** (2026-08-30 신설).
    #:
    #: `plan[].run_id` 와 **다른 것이다** — 저쪽은 그 *부서 호출* 의 id 이고,
    #: 이것은 *마스터 실행 한 번* 의 id 다. 이름이 겹쳐 헷갈리므로 여기서는
    #: `history_run_id` 로 부른다 (DB 컬럼은 `master_agent_runs.run_id`).
    #:
    #: 화면이 승인할 때 이 값을 되돌려 준다 — 그래야 *"내가 본 그것을 승인했다"* 가
    #: 기록된다. 없으면 서버가 최신 실행을 고르는데, 그 사이 재실행이 있었으면
    #: **사람이 본 것과 다른 안이 승인된 것으로 남는다.**
    #:
    #: 적재에 실패하면 `None` 이다 — 이력이 없어도 계산 결과는 돌려준다.
    history_run_id: str | None = None

    end_code: EndCode
    reason: str

    scenarios: list[dict[str, Any]] = []
    judgment: dict[str, Any] = Field(
        default={},
        description=(
            "매입 제안의 판정부 — `scenarios` 를 뺀 제안 최상위 전부 "
            "(situation · allowed_axes · confidence · meta · no_proposal_reason …). "
            '"왜 3안인지/2안인지"의 근거이며 프론트 판정 헤더가 소비한다. '
            "`verdicts`(조언자·검증 판정)와 다르다 — 이건 **매입 자신의** 판정이다."
        ),
    )
    constraints: dict[str, dict[str, Any]] = {}
    verdicts: dict[str, dict[str, Any]] = {}

    evidences: list[EvidenceOut] = Field(
        default=[],
        description=(
            "부서가 낸 근거 - 시나리오 숫자의 출처. **마스터가 고르거나 요약하지 않는다.** "
            "`mode` 로 경계 근거(PRE_PURCHASE)와 판정 근거(SCENARIO_VALIDATION)를 가른다. "
            "비어 있으면 '근거가 완비됐다' 가 아니라 **부서가 근거를 안 냈다**는 뜻이다."
        ),
    )

    adjustments: list[AdjustmentOut] = Field(
        default=[],
        description=(
            "부서가 낸 조정안 - **개수가 아니라 내용이다.** 마스터가 고르거나 정렬하지 "
            "않고 온 차례 그대로 싣는다. 되먹임 계약 §3.2 의 `constraint` 가 이 배열이다. "
            "비어 있는 것이 곧 실패는 아니다 - 물류는 `reject` 안의 조정을 승격하지 "
            "않으므로(#121) 0건이 정답인 날이 있다."
        ),
    )

    blocked_by: list[AgentName] = []
    blocked_failures: list[BlockedAgentOut] = Field(
        default=[],
        description=(
            "막은 부서가 **왜** 막았는가. `blocked_by` 와 같은 부서를 가리키되 사유를 "
            "함께 든다. 비어 있는데 `blocked_by` 가 차 있으면 Flow 밖에서 막힌 것이다 "
            "(어댑터 미등록)."
        ),
    )
    findings: list[str] = Field(
        default=[],
        description="**매입 재호출을 유발한** 발견. 다시 만들면 달라질 수 있는 것만 여기 든다.",
    )
    concerns: list[str] = Field(
        default=[],
        description=(
            "사실이지만 **재호출로 고쳐지지 않는** 것 — 조언자의 계약 위반 · 마스터 "
            "배선 문제. 사람이 봐야 한다 (§3.4)."
        ),
    )
    skipped_checks: list[str] = Field(
        default=[],
        description=(
            "검증 Tool 이 **판정하지 못한** 검사와 사유. 비어 있는 findings 를 "
            "'전부 통과'로 읽지 않게 한다 (§3.7.6 커버리지를 감추지 않는다)."
        ),
    )
    verification_skipped: bool = False
    purchase_attempts: int = 0

    presentable: bool = False
    single_option: bool = False

    plan: list[StepOut] = []
    plan_signature: list[tuple[str, str, int]] = Field(
        default=[],
        description="누구를 어떤 목적으로 몇 번째로 불렀는가. 같은 입력에 같은 값이어야 한다.",
    )
    missing_adapters: list[AgentName] = Field(
        default=[],
        description="어댑터가 아직 등록되지 않은 에이전트. 비어 있지 않으면 end_code 는 E4 다.",
    )

    report_text: str = Field(
        default="",
        description=(
            "사람이 읽는 리포트 (마스터 역할 ⑥). **규칙만으로 만든다** — 숫자·결론은 "
            "종료 코드와 부서 값에서 그대로 온다. 발화문 경로에서는 여기에 LLM 이 쓴 "
            "한 문장이 앞에 얹힌다."
        ),
    )

    input_sources: dict[str, str] = Field(
        default={},
        description=(
            "마스터가 실어 준 입력 3종의 출처 — `등급:소스` (§3.2.5 · `inputs.py`). "
            "등급은 MEASURED(실 DB 그대로) · DERIVED(실 DB 값에서 파생) · MOCK · MISSING. "
            "**같은 값이라도 어디서 왔느냐로 판단의 무게가 다르다** — 값만 실으면 "
            "리포트를 읽는 사람이 전부 실측으로 읽는다."
        ),
    )
    mocked_inputs: list[str] = Field(
        default=[],
        description=(
            "🔴 mock 에서 온 입력. **비어 있지 않으면 이 실행의 결론을 실측으로 읽으면 "
            "안 된다.** 검증 커버리지를 분수로 내는 것과 같은 이유로 감추지 않는다."
        ),
    )


class TriggerAck(BaseModel):
    """ML 완료 이벤트 수신 확인."""

    accepted: bool
    request_id: str
    as_of: date
    note: Literal["queued", "executed"] = "executed"
