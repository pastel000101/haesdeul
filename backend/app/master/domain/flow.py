"""매입 Flow 의 결과 모델과 조언 부서 목록 — Flow 실행(부서 호출 순서)은 `service/flow.py`.

★ 2026-09-30 재구성 BL-018: `master/flow.py` 에서 옮겼다 — `ADVISORS`, `ProcurementOutcome`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.contracts.core import EndCode, SuggestedAdjustment
from app.contracts.envelope import AgentFailure, AgentName, SourcedEvidence
from app.master.domain.plan import ExecutionPlan

ADVISORS: tuple[AgentName, ...] = ("finance", "inventory")
"""1차 조언자. 영업은 구성에서 빠졌고 판매는 2차 MVP 다 (정의서 §2.1)."""


@dataclass(frozen=True)
class ProcurementOutcome:
    """Flow 한 번의 결과. **무엇을 못 했는지도 담는다.**"""

    end_code: EndCode
    reason: str
    plan: ExecutionPlan

    scenarios: tuple[Mapping[str, Any], ...] = ()
    judgment: Mapping[str, Any] = field(default_factory=dict)
    constraints: Mapping[AgentName, Mapping[str, Any]] = field(default_factory=dict)
    verdicts: Mapping[AgentName, Mapping[str, Any]] = field(default_factory=dict)

    #: 🔴 **부서가 낸 근거.** 값이 어디서 왔는지를 사람이 볼 수 있게 나른다 (2026-09-02).
    #:
    #: 전에는 `_collect_constraints` 가 모아 **검증에만** 넘기고 응답에서는 끊겼다.
    #: 화면은 "재무 상한 2,000만원" 은 보여주는데 그 숫자의 출처는 못 보여줬다 -
    #: 사유 문장(`verdicts[].reasoning`)은 사람이 쓴 설명이지 출처가 아니다.
    #:
    #: ★ **마스터는 고르지도 요약하지도 않는다.** `constraints`·`verdicts` 를 나르는
    #:   것과 같다 - 고르는 것이 곧 판단이다 (§3.2.2).
    #:
    #: ★ **모드를 함께 담는다.** 경계 근거(`PRE_PURCHASE`)와 판정 근거
    #:   (`SCENARIO_VALIDATION`)는 답하는 질문이 다르다 - 앞은 "상한이 왜 그 값인가",
    #:   뒤는 "이 안이 왜 ok 인가". 한 칸에 섞으면 화면이 둘을 구별하지 못한다.
    evidences: tuple[SourcedEvidence, ...] = ()

    #: 🔴 **부서가 낸 조정안 - 봉투 표준형 그대로** (2026-09-02).
    #:
    #: 전에는 `_validate` 가 `len()` 만 담고 객체를 버렸다. 사실이 아주 사라진 것은
    #: 아니어서(부서 원시형이 `verdicts[].payload` 에 남는다) 더 위험했다 -
    #: **"값이 있으니 되겠지" 로 넘어가면 마스터가 남의 payload 를 파게 된다.**
    #: 표준형은 그 해석을 안 하려고 만든 자리인데 그 자리를 비워 두고 있었다.
    #:
    #: ★ **감싸지 않는다.** `SuggestedAdjustment` 는 `dept` 를 스스로 들고 있어
    #:   `SourcedEvidence` 같은 껍데기가 필요 없다. 되먹임 계약 §3.2 의 `constraint`
    #:   가 부서를 가로지르는 평평한 배열이라 모양도 1:1 로 맞는다.
    #:
    #: ★ **개수를 따로 담지 않는다.** 세는 쪽이 센다 - 같은 사실의 주인을 둘로
    #:   만들지 않는다 (`evidences` 와 같다).
    adjustments: tuple[SuggestedAdjustment, ...] = ()

    blocked_by: tuple[AgentName, ...] = ()

    #: 🔴 **막은 부서가 왜 막았는가** (2026-09-02). `blocked_by` 는 이름만 든다.
    #:
    #: `reason` 문장에도 같은 내용이 들어가지만 문장은 사람이 읽는 것이고, 이쪽은
    #: 화면이 부서별로 펼치기 위한 것이다 — `AdvisorVerdicts` 가 판정 사유를 펴는
    #: 자리와 같다.
    blocked_failures: tuple[AgentFailure, ...] = ()

    findings: tuple[str, ...] = ()
    concerns: tuple[str, ...] = ()
    skipped_checks: tuple[str, ...] = ()
    verification_skipped: bool = False
    purchase_attempts: int = 0

    @property
    def presentable(self) -> bool:
        """사용자에게 선택지를 올릴 수 있는가."""
        return self.end_code == "E1_APPROVED" and bool(self.scenarios)

    @property
    def single_option(self) -> bool:
        """선택지가 하나뿐인가.

        §1.2-7 은 2개 이상을 요구하지만 §5.2 가 단일안 예외를 둔다.
        **사용자에게 보여줄지 자체가 미결(M-5)** 이라 여기서는 사실만 드러낸다.
        """
        return len(self.scenarios) == 1
