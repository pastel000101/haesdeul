"""매입 · 판매 실행 응답이 함께 쓰는 부품 모델 — 근거 · 단계 · 조정 · 막힌 부서."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.contracts.envelope import AgentName


class EvidenceOut(BaseModel):
    """부서가 낸 근거 하나. 응답에 나온 숫자가 어디서 왔는지를 나른다.

    `verdicts[].reasoning` 은 부서가 쓴 설명 문장이고 출처가 아니다. 출처는 이 모델이
    나른다. 같은 근거가 검증 Tool 에도 넘어간다.

    마스터는 근거를 고르거나 요약하지 않고 부서가 낸 것을 그대로 옮긴다. 고르는 것이 곧
    판단이라, 고르는 순간 화면의 근거가 마스터의 의견이 된다.

    `evidence_grade` 는 값만큼 중요하다. 같은 숫자라도 `OFFICIAL` 과 `ASSUMED` 는 판단의
    무게가 다르다. `input_sources` 를 등급까지 싣는 것과 같은 이유다.
    """

    agent: AgentName
    #: 어느 호출에서 나온 근거인가. `PRE_PURCHASE` 는 경계("상한이 왜 그 값인가"),
    #: `SCENARIO_VALIDATION` 은 판정("이 안이 왜 ok 인가") - 답하는 질문이 다르다.
    mode: str

    #: 무엇에 대한 근거인가 (예: `finance_cap_amount_krw`).
    claim: str
    #: 어디서 온 값인가 - inventory · sales · finance · documents · tool_calc · persona.
    source: str
    #: `float | str` 이다. 계약(`app/contracts/core.py` 의 `Evidence.value`)은 `float` 인데
    #: 실제로는 문자열이 오는 근거가 있다 - 재무 `policy_version_used` 가
    #: `"v1.3-PROVISIONAL"` 을 싣는다 (`finance/service/capabilities/procurement.py`).
    #: `Evidence` 가 dataclass 라 런타임 검증이 없다.
    #:
    #: 마스터는 값을 고치지도 버리지도 않는다. 고치면 남의 값을 덮어쓰는 것이고
    #: (§3.2.2), 버리면 근거를 고르는 것이다. 원본을 나르고 어긋난 사실은 `concerns` 로
    #: 드러낸다 - 재호출로 안 고쳐지는 남의 계약 문제라 정확히 concerns 의 자리다.
    value: float | str
    unit: str
    #: OFFICIAL · VENDOR · SIM_FIXED · ASSUMED · INVALID_FOR_HARD.
    evidence_grade: str
    #: SIM_FIXED 는 여기에 승인 회차가 적힌다.
    evidence_detail: str = ""
    #: 원본 레코드 참조. 비어 있을 수 없다 - 봉투가 막는다 (§1.2-5).
    ref_ids: list[str] = []


class StepOut(BaseModel):
    """실행 계획의 한 걸음. 실행 시각을 담지 않는다 — 재현성 비교 대상이기 때문이다."""

    seq: int
    agent: AgentName
    mode: str
    call_seq: int
    run_id: str
    runtime_status: str
    business_status: str
    used_tools: list[str] = []
    finding_codes: list[str] = []
    missing_data: list[str] = []

    #: 부서가 밝힌 사유. `missing_data` 가 "무엇이 없어서" 라면 이건 "왜 터졌는지" 다.
    #: 없으면 이력을 파도 `runtime_status=ERROR` 까지만 알고 그 ERROR 의 사유는 어디에도
    #: 없다 (재현성 측정 2026-09-02).
    reasoning: str = ""

    #: 그 부서 안에서 LLM 이 돌았나. 없으면 부서가 규칙으로 답한 것과 모델로 답한 것이
    #: 화면에서 같아 보인다.
    llm_status: str = "DISABLED"
    llm_model: str = ""
    llm_attempts: int = 0
    llm_fallback_used: bool = False

    #: 부서가 계획을 다시 세운 횟수. `llm_attempts` 와 뜻이 다르다.
    #: `llm_attempts` 는 Planner + Finalizer 호출 수라 툴 개수를 따라 커지고
    #: 재시도가 아니다 (재무 정정 2026-09-02). 재계획은 이 값이다.
    replans: int = 0

    #: 그 부서가 밝힌 관측 기준시점. 주인은 `AgentReply.observed_at`.
    #:
    #: 위 docstring 의 「시각을 담지 않는다」를 안 깬다. 저 말은 "언제 돌렸나"
    #:   (실행 시각)를 안 담는다는 뜻이고, 이 값은 업무 사실이 언제부터 알려졌나다 —
    #:   같은 입력을 같은 계획으로 다시 돌리면 같은 값이 난다.
    #:
    #: `None` 이 「안 쟀다」다. 화면이 `as_of` 나 오늘 날짜로 메우지 않는다 — 메우면
    #:   아직 안 실은 부서가 실은 부서처럼 보인다.
    observed_at: date | None = None

    #: 부서가 스스로 남긴 관측. 마스터는 읽지 않고 나른다.
    #:
    #: 값은 `ExecutionMetadata` → `ExecutionStep` 을 거쳐 여기까지 온다 (#165). 재무가
    #: provider 대체 사실(gemini → ollama · HTTP_429)을 여기 싣는다 — 이 칸이 없으면
    #: 응답·화면·매입 이력 어디에도 안 나간다.
    #:
    #: 주의: 부서마다 모양이 다른 JSON 문자열이다. 마스터도 화면도 파싱하지 않는다 -
    #: 파싱하면 부서 스키마가 한 벌 더 생기고, 부서가 필드를 바꾸는 날 이쪽만
    #: 옛말을 한다 (`AdvisorVerdicts` 가 부서 payload 를 안 펴는 것과 같은 이유).
    observations: list[str] = []


class AdjustmentOut(BaseModel):
    """부서가 낸 조정안 하나. 봉투의 표준형을 그대로 싣는다.

    되먹임 계약의 `constraint` 가 이 객체 배열이다.

    부서 원시형이 아니다. 같은 사실이 `verdicts[].payload` 에도 부서 고유 모양으로
    남아 있지만, 그쪽을 해석해 쓰면 마스터가 다른 부서의 스키마를 해석하게 된다.
    표준형은 그 해석 없이 읽도록 둔 자리다.
    """

    dept: str = Field(description="AgentName 이 아니라 Dept 다. 어휘가 다르다 (_AGENT_DEPT).")
    axis: str = Field(description="quantity · timing · channel_mix · amount. 봉투가 강제한다.")
    target_value: float
    unit: str = Field(
        description=(
            "kg · krw · d. 닫힌 집합이 아니다 — 봉투가 값을 검사하지 않는다. "
            "물류 타이밍 축은 봉투 as_of 로부터의 일수를 'd' 로 싣는다."
        )
    )
    reason: str = Field(description="부서가 쓴 문장 그대로. 마스터가 요약하지 않는다.")
    ref_ids: list[str]

    #: 이 조정이 어느 시나리오 대상인가 (계약 v0.2 §5.1).
    #: 칸으로 두어야 기계가 부서의 `reason` 문장을 파싱하지 않고 읽는다.
    #: 합쳐진 건이면 합쳐진 라벨을 다 담는다 - 건수를 안 늘리면서 사실이 드러난다.
    scenario_labels: list[str] = []

    #: 어느 회차의 상한인가 (계약 v0.2 §5.2).
    #: 번호가 아니라 날짜다 - 물류에 회차 번호가 없어 번호 칸을 두면 없는 값을
    #: 만들게 된다. 회차 개념이 없는 축(재무 amount)은 `null` 이다.
    split_date: date | None = None


class BlockedAgentOut(BaseModel):
    """기여하지 못한 부서 하나와 그 사유.

    `blocked_by` 는 부서 이름만 담는다. 이름만 받으면 다시 실행해 보는 것 말고는 할 수
    있는 일이 없으므로, 이 모델이 상태 · 부서가 밝힌 사유 · 없는 입력을 함께 싣는다.
    """

    agent: AgentName
    runtime_status: str = Field(
        description="ERROR · RUNTIME_NOT_READY, 그리고 아예 안 불린 경우 NOT_CALLED."
    )
    reasoning: str = ""
    missing_data: list[str] = []
    detail: str = Field(
        description=(
            "사람이 읽는 한 줄. 서버가 한 곳에서 만들며 `reason` 문장에 들어간 것과 "
            "같은 값이다. 화면이 따로 조립하지 않으므로 두 문장이 갈리지 않는다."
        )
    )
