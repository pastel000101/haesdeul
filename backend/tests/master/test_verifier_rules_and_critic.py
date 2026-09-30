"""검증 Tool 의 두 층 — 규칙(domain)과 Critic 호출(service)을 합치는 순서.

2026-09-30 재구성 BL-018 에서 규칙부를 `app/master/domain/verifier.py` 로 뗐다. 규칙 결과와
Critic 결과를 합치는 이음매가 새로 생긴 자리라 여기서 붙잡는다.

```text
findings  = 규칙 findings + Critic findings
concerns  = 규칙 concerns + Critic concerns          (Critic 이 죽으면 CRITIC 오류 한 줄)
skipped   = 규칙 skipped  + Critic skipped           (못 돌렸으면 사유 한 줄) + 미구현 검사 고지
Critic    = 많아야 한 번
```

★ 규칙 안의 순서도 붙잡는다 — 실행 계획(M16) → 판정 못 낸 조언자 → timing → 항등식 →
  지급 일정 → 실어 준 값. 사람이 읽는 `concerns` 줄 순서가 이것이다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.contracts.core import Evidence
from app.master.adapters.critic_bridge import fold
from app.master.critic.schemas import (
    ConcernOut,
    CriticProcurementRequest,
    CriticVerdictOut,
    FindingOut,
)
from app.master.domain.plan import ExecutionPlan, ExecutionStep
from app.master.domain.verifier import UNCOVERED_CHECKS, VerificationResult, check_proposal
from app.master.service.verifier import MasterVerifier, VerificationContext

AS_OF = date(2025, 12, 31)
ADVISORS = ("finance", "inventory")


def _split_scenario(**over: Any) -> dict:
    """분할 2회차 — 항등식 · 지급 일정이 성립하는 모양(매입 실측 스키마)."""
    base = {
        "label": "분할",
        "strategy_type": "timing",
        "coverage_days": 5,
        "total_qty_kg": 2000,
        "total_amount_krw": 3_300_000,
        "max_price": 1750,
        "split_plan": [
            {"seq": 1, "date": "2026-01-01", "qty_kg": 1000},
            {"seq": 2, "date": "2026-01-02", "qty_kg": 1000},
        ],
        "sourcing_plan": [
            {"market": "가락", "grade": "상", "qty_kg": 2000, "grade_unit_price": 1650}
        ],
        "rationale": [],
        "risks": [],
    }
    base.update(over)
    return base


#: `timing` 축이 닫혔는데 분할이라 L-TIMING-GATE 가 울린다. 지급 일정이 없어 L-PAYSCHED 는
#: 미검사다. 항등식은 성립하므로 Critic 은 돈다.
PROPOSAL = {"scenarios": [_split_scenario()], "allowed_axes": ["quantity"], "confidence": "high"}

CONSTRAINTS = {
    "finance": {"finance_cap_amount_krw": 31_854_627.0, "purchase_payment_days": 7},
    "inventory": {
        "warehouse_free_kg": 7636.72,
        "inbound_lead_days": 2.0,
        "cap_by_date": {"2026-01-03": 5000.0},
        "cap_by_date_window_days": 18,
    },
}

#: 물류가 판정을 못 냈다 — ADVISOR-NO-VERDICT
VERDICTS = {
    "finance": {"business_status": "ok", "runtime_status": "READY"},
    "inventory": {"business_status": "skipped", "runtime_status": "RUNTIME_NOT_READY"},
}


def _step(seq: int, agent: str, mode: str, codes: tuple[str, ...] = ()) -> ExecutionStep:
    return ExecutionStep(
        seq=seq,
        agent=agent,
        mode=mode,
        call_seq=seq,
        run_id=f"{agent.upper()}-{seq}",
        runtime_status="READY",
        business_status="ok",
        finding_codes=codes,
    )


def _plan() -> ExecutionPlan:
    """물류 경계를 안 불렀고(M16-AGENT-MISSING), 매입 회신에 봉투 위반이 남았다(M16-ENVELOPE)."""
    return ExecutionPlan(
        request_id="REQ-1",
        as_of=AS_OF,
        steps=[
            _step(1, "finance", "PRE_PURCHASE"),
            _step(2, "purchase", "GENERATE_SCENARIOS", ("E-REASONING-EMPTY",)),
            _step(3, "finance", "SCENARIO_VALIDATION"),
        ],
    )


def _context() -> VerificationContext:
    return VerificationContext(
        as_of=AS_OF,
        item="배추",
        evidences={
            "finance": (
                Evidence(
                    claim="finance_cap_amount_krw",
                    source="finance",
                    ref_ids=("FIN-STATE-1",),
                    value=31_854_627.0,
                    unit="KRW",
                ),
            ),
        },
    )


def _verdict() -> CriticVerdictOut:
    return CriticVerdictOut(
        cycle="A",
        as_of=AS_OF,
        run_seq=1,
        scenario_id="S-1",
        runtime_status="READY",
        status="FAIL",
        badge="판정",
        coverage={"L1": (1, 13)},
        coverage_ratio=(1, 56),
        findings=[
            FindingOut(layer="L1", check_id="L1-X", detail="대역 발견", dept=None, route=None)
        ],
        concerns=[ConcernOut(code="C-X", detail="대역 우려", layer="L2", dept=None)],
        skipped=["대역 생략"],
        end_stage=None,
    )


class _Spy:
    """Critic 대역 — 몇 번 불렸는지 센다."""

    def __init__(self, raises: Exception | None = None) -> None:
        self.calls: list[CriticProcurementRequest] = []
        self.raises = raises

    def __call__(self, req: CriticProcurementRequest) -> CriticVerdictOut:
        self.calls.append(req)
        if self.raises is not None:
            raise self.raises
        return _verdict()


def _rules() -> VerificationResult:
    checks = check_proposal(PROPOSAL, CONSTRAINTS, VERDICTS, _plan(), ADVISORS)
    assert not checks.identity_broken
    return checks.result


def test_merges_rules_then_critic_then_uncovered_notice():
    rules = _rules()
    # 픽스처가 세 갈래를 모두 채우는지 먼저 본다 — 비면 순서 검사가 공짜로 통과한다
    assert rules.findings and rules.concerns and rules.skipped
    critic_findings, critic_concerns, critic_skipped = fold(_verdict())
    spy = _Spy()

    result = MasterVerifier(critic=spy)(PROPOSAL, CONSTRAINTS, VERDICTS, _plan(), _context())

    assert result == VerificationResult(
        (*rules.findings, *critic_findings),
        (*rules.concerns, *critic_concerns),
        (*rules.skipped, *critic_skipped, *UNCOVERED_CHECKS),
    )
    assert len(spy.calls) == 1, "Critic 은 한 번만 부른다"


def _broken_proposal() -> dict:
    return {**PROPOSAL, "scenarios": [_split_scenario(total_qty_kg=2100)]}


@pytest.mark.parametrize(
    ("critic", "proposal", "context", "reason"),
    [
        (None, PROPOSAL, _context(), "Critic L0~L5 (56검사): 검증 Tool 에 주입되지 않음"),
        (
            "spy",
            PROPOSAL,
            None,
            "Critic L0~L5 (56검사): 실행 맥락 미전달 — as_of · 품목 · 근거 없음",
        ),
        (
            "spy",
            _broken_proposal(),
            _context(),
            (
                "Critic L0~L5 (56검사): 시나리오 항등식이 깨져 돌리지 않았다 — "
                "어긋난 숫자 위의 판정은 통과해도 뜻이 없다"
            ),
        ),
    ],
    ids=["critic_none", "context_none", "identity_broken"],
)
def test_skipped_critic_adds_one_reason_before_uncovered_notice(critic, proposal, context, reason):
    spy = _Spy() if critic == "spy" else None
    rules = check_proposal(proposal, CONSTRAINTS, VERDICTS, _plan(), ADVISORS).result

    result = MasterVerifier(critic=spy)(proposal, CONSTRAINTS, VERDICTS, _plan(), context)

    assert result == VerificationResult(
        rules.findings, rules.concerns, (*rules.skipped, reason, *UNCOVERED_CHECKS)
    )
    assert spy is None or spy.calls == [], "생략한 Critic 을 불렀다"


def test_failed_critic_appends_error_concern_and_skip_before_notice():
    rules = _rules()
    spy = _Spy(raises=RuntimeError("boom"))

    result = MasterVerifier(critic=spy)(PROPOSAL, CONSTRAINTS, VERDICTS, _plan(), _context())

    assert result == VerificationResult(
        rules.findings,
        (*rules.concerns, "CRITIC: 검증 Tool 이 돌지 못했다 — RuntimeError: boom"),
        (*rules.skipped, "Critic L0~L5 (56검사): 실행 중 오류로 미판정", *UNCOVERED_CHECKS),
    )
    assert len(spy.calls) == 1


def _first(lines: tuple[str, ...], code: str) -> int:
    return next(i for i, line in enumerate(lines) if line.startswith(code))


def test_rule_lines_keep_their_order():
    """실행 계획 → 판정 못 낸 조언자 → timing → 항등식 → 지급 일정 → 실어 준 값."""
    scenario = _split_scenario(
        total_qty_kg=2100,  # 항등식이 깨진다
        risks=["inbound_lead_days 미확정"],  # 실어 준 값을 미결이라 답한다
        payment_schedule=[
            {"seq": 1, "purchase_date": "2026-01-01", "payment_date": "2026-01-09"},
            {"seq": 2, "purchase_date": "2026-01-02", "payment_date": "2026-01-09"},
        ],
    )
    proposal = {"scenarios": [scenario], "allowed_axes": ["quantity"]}

    checks = check_proposal(proposal, CONSTRAINTS, VERDICTS, _plan(), ADVISORS)
    findings, concerns = checks.result.findings, checks.result.concerns

    assert checks.identity_broken
    finding_codes = ("M16-ENVELOPE", "L-TIMING-GATE", "L-IDENTITY-QTY", "L-PAYSCHED-N5")
    assert [_first(findings, c) for c in finding_codes] == sorted(
        _first(findings, c) for c in finding_codes
    ), findings
    concern_codes = ("M16-AGENT-MISSING", "ADVISOR-NO-VERDICT", "SUPPLIED-BUT-UNRESOLVED")
    assert [_first(concerns, c) for c in concern_codes] == sorted(
        _first(concerns, c) for c in concern_codes
    ), concerns
