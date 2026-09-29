"""마스터 에이전트 (정의서 v2.2 · 소유: 이현서).

마스터 ↔ 도메인 에이전트가 주고받는 **공용 계약**(봉투, M-1 공통 이벤트 규약 v0.2)은
`app/contracts/envelope.py` 에 있다 (2026-09-29 재구성 BL-011 — 전에는 이 패키지의
`envelope` 모듈이었고 여기서 그 이름들을 다시 내보냈다). 파트가 봉투를 쓰려고 마스터를
import 하지 않게 옮긴 것이라, 이 패키지는 봉투 이름을 다시 내보내지 않는다.

이 패키지의 모듈은 **마스터 본체**다.

    ports    에이전트 호출 접점 · 레지스트리 · 실패를 값으로
    budget   호출 예산 강제 (정의서 §1.2-12)
    plan     실행 계획 기록 (정의서 §1.2-11)
    runner   위 셋을 묶은 호출 계층
    flow     매입 의사결정 Flow (정의서 §3.4)
    wiring   프로세스 전역 에이전트 레지스트리 — 각 파트 어댑터가 등록한다
    persistence  실행 계획 적재 (정의서 §1.2-11)
    router   API — /master/request · /master/trigger · /master/runs/{request_id}
"""

from app.master.budget import BudgetExhausted, CallBudget
from app.master.flow import (
    ADVISORS,
    ProcurementFlow,
    ProcurementOutcome,
    VerifierPort,
)
from app.master.plan import ExecutionPlan, ExecutionStep
from app.master.ports import (
    AgentNotRegistered,
    AgentPort,
    AgentRegistry,
    MasterError,
    empty_metadata,
    error_reply,
)
from app.master.runner import MasterRunner

__all__ = [
    "ADVISORS",
    "AgentNotRegistered",
    "AgentPort",
    "AgentRegistry",
    "BudgetExhausted",
    "CallBudget",
    "ExecutionPlan",
    "ExecutionStep",
    "MasterError",
    "MasterRunner",
    "ProcurementFlow",
    "ProcurementOutcome",
    "VerifierPort",
    "empty_metadata",
    "error_reply",
]
