"""현재 승인 모델 — 결정 · 실행 행 · 약정을 한데 묶은 값.

★ 2026-09-30 재구성 BL-018: `master/decision_service.py` 에서 옮겼다 — `CurrentApproval`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.contracts.commitment import ApprovedCommitment
from app.master.schemas.decision import CommitmentOut, DecisionOut


@dataclass(frozen=True)
class CurrentApproval:
    """현재 유효한 승인 하나와 **그 실행으로 재조립한 선정안 약정** (기록 덮기 전).

    ★ 실매입 기록이 이것을 쓴다 — 폼의 기본값(선정안)과 기록을 덮을 바탕이 같은
      재조립에서 나와야 둘이 안 갈린다.
    """

    decision: DecisionOut
    cycle: str
    #: 결정이 가리키는 실행 이력 행. 재검증이 정책판 · 품목을 여기서 읽는다.
    run_row: dict[str, Any]
    response_payload: dict[str, Any]
    #: 그 실행의 기준일. 실매입 매입일의 하한이다.
    as_of: date | None
    #: N5 원문 (`constraints.finance.purchase_payment_days`). 약정 조립이 읽는 그 값이다.
    purchase_payment_days: Any
    sim_run_id: str | None
    #: 선정안 약정의 응답 모양. 못 만들었으면 `buildable=False` 와 사유.
    plan_out: CommitmentOut | None
    #: 선정안 약정 객체. 못 만들었으면 `None`.
    plan: ApprovedCommitment | None
