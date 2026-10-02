"""
verifier.py — 마스터 검증 Tool 의 규칙부 (정의서 §3.7)

    ② 마스터 자신의 계산 재검산  ← 시나리오 항등식 · 분할 지급 일정
    ③ 합쳤을 때의 모순          ← timing 게이트 · 실어 준 값을 미결로 답함
    ④ 실행 계획 온전성 (M-16)   ← 여기서 전부 구현한다

받은 값(제안 · 조언자 경계 · 판정 · 실행 계획)만 보고 `findings` · `concerns` · `skipped` 를
낸다. DB · HTTP · LLM · 파일 · 환경변수 · 시계를 읽지 않고, Critic 도 부르지 않는다 — Critic
56검사를 부르는 자리와 규칙 · Critic 을 도는 순서는 `app/master/service/verifier.py` 다.

판정하지 못한 것을 판정했다고 말하지 않는다 (§3.7.6).
도메인 payload 필드명이 확정되지 않은 검사는 `findings` 를 비우는 것이 아니라
`skipped` 에 사유와 함께 남긴다. 비워 두면 "검사했고 통과했다"로 읽힌다.

왜 ①이 여기 없는가
`E-BIND-*` · `E-EVIDENCE-*` · `E-REASONING-*` 는 `MasterRunner.call()` 이 호출마다
돌려 `ExecutionStep.finding_codes` 에 쌓는다. 여기서는 그것이 남아 있는지만 본다
(`M16-ENVELOPE`) — 두 번 계산하지 않는다.

필수 조언자 목록은 인자로 받는다(기본값은 `service/verifier.py` 의 `MasterVerifier` 가 쥔다).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.contracts.envelope import ENVELOPE_META_KEYS, AgentName
from app.master.domain.plan import ExecutionPlan


@dataclass(frozen=True)
class VerificationResult:
    """세 갈래로 나눠 돌려준다.

    | | 무엇 | 마스터 행동 |
    |---|---|---|
    | `findings` | 매입이 다시 만들면 달라질 수 있는 것 | 재호출 |
    | `concerns` | 사실이지만 재호출로 안 고쳐지는 것 | 보고만 |
    | `skipped` | 판정하지 못한 것 | 커버리지에 노출 |

    `concerns` 를 나눈 이유: 전부 `findings` 로 두면 재무 회신의 봉투 위반 때문에
    매입을 다시 부르게 된다 — 매입이 몇 번을 다시 만들어도 재무의
    `E-EVIDENCE-MISSING` 은 그대로다. 호출 예산만 태우고 `E3_REJECTED` 로 끝난다.

    재호출은 "다시 부르면 달라질 수 있는 것"에만 쓴다. 남의 계약 위반과 마스터
    자신의 배선 문제는 보고 대상이지 재시도 대상이 아니다.

    `concerns` 는 숨기는 자리가 아니다. 응답에 그대로 나가고, 사람이 본다
    (§3.4 "마스터는 최적안을 고르지 않는다").
    """

    findings: tuple[str, ...] = ()
    concerns: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        """재호출을 유발하지 않는가. `concerns` 는 여기에 안 든다."""
        return not self.findings


@dataclass(frozen=True)
class ProposalChecks:
    """Critic 을 부르기 전 규칙 검사의 결과.

    `identity_broken` 을 따로 드는 이유 — 시나리오 항등식이 깨졌으면 service 가 Critic 을
    부르지 않는다. 어긋난 숫자 위에서 돌린 56검사는 그럴듯한 통과를 만든다.
    """

    result: VerificationResult
    identity_broken: bool


#: 아직 이 경로에 붙지 않은 검사. service 가 Critic 결과 뒤에 `skipped` 로 싣는다.
#:
#: 이 줄이 없으면 `findings: []` 가 "56검사를 통과했다"로 읽힌다.
#: 붙지 않은 것을 조용히 두는 것이 커버리지를 감추는 가장 흔한 방식이다.
UNCOVERED_CHECKS: tuple[str, ...] = (
    "②마스터 계산 재검산: 결합·클리핑 Tool 이 Flow 에 붙은 뒤 가능",
)


def check_proposal(
    proposal: Mapping[str, Any],
    constraints: Mapping[AgentName, Mapping[str, Any]],
    verdicts: Mapping[AgentName, Mapping[str, Any]],
    plan: ExecutionPlan,
    required_advisors: tuple[AgentName, ...],
) -> ProposalChecks:
    """규칙 여섯을 정해진 순서로 돌린다. 각 갈래 안의 줄 순서가 이 순서다.

    시나리오 배열이 아니라 제안 전체를 받는다 (2026-08-27 매입 스키마 확인).
    `allowed_axes` · `situation` · `confidence` 는 `scenarios[]` 안이 아니라
    제안 최상위에 있다(`PurchaseProposal`). 배열만 받으면 그 판정들을 볼 수 없다.
    """
    scenarios = _scenarios_of(proposal)
    findings: list[str] = []
    concerns: list[str] = []
    skipped: list[str] = []

    _check_plan_integrity(plan, scenarios, required_advisors, findings, concerns)
    _check_advisor_answered(verdicts, concerns)
    _check_timing_gate(proposal, scenarios, findings, skipped)
    identity_findings = len(findings)
    _check_scenario_identities(scenarios, findings, skipped)
    identity_broken = len(findings) > identity_findings
    _check_payment_schedule(scenarios, constraints, findings, skipped)
    _check_supplied_but_unused(scenarios, constraints, concerns)

    return ProposalChecks(
        VerificationResult(tuple(findings), tuple(concerns), tuple(skipped)), identity_broken
    )


def supplied_but_unresolved(
    scenarios: Sequence[Mapping[str, Any]],
    constraints: Mapping[AgentName, Mapping[str, Any]],
) -> list[str]:
    """다른 파트가 부를 수 있는 자리. 마스터가 실어 준 값을 부서가 "없다" 고
    답하는지 본다 — 마스터 관통이 쓰는 것과 같은 코드다.

    매입이 8/31 에 겪은 일 때문에 열었다.

    ```text
    매입 검사   마스터 정규식을 빌려 "우리가 재현한 규칙에 걸리는가" 를 봤다  → 통과
    실물        concerns 0건
    ```

    관문이 둘인데 하나만 재현했다. 정규식(둘째)에 걸려도 `supplied` 조회(첫째)에서
    이미 걸러지면 아무 일도 안 일어난다 — `operational_limit_days` 는 중첩이라
    그때 마스터의 `supplied` 에 없었다.

    규칙을 재현하지 마십시오. 재현한 것이 진짜와 갈리는 순간, 검사는 통과하는데
    실물은 조용해집니다. 이 함수를 부르면 재현할 것이 없습니다.

    ```python
    from app.master.domain.verifier import supplied_but_unresolved

    concerns = supplied_but_unresolved(scenarios, constraints)
    assert any("operational_limit_days" in c for c in concerns)
    ```

    검증 service(`MasterVerifier`)를 거치지 않아도 된다 — 이 검사는 Critic 도 DB 도 안 탄다.
    """
    concerns: list[str] = []
    _check_supplied_but_unused(tuple(scenarios), constraints, concerns)
    return concerns


def unresolved_supplied_keys(
    scenarios: Sequence[Mapping[str, Any]],
    constraints: Mapping[AgentName, Mapping[str, Any]],
) -> list[str]:
    """지목된 키만. 위 함수의 문장 대신 사실 을 돌려준다.

    매입이 2026-08-31 에 겪은 두 번째 일 때문에 연다.

    ```text
    concern 문장이 사유 원문을 통째로 되싣는다 (…사유: <매입이 쓴 문장>)
      → "operational_limit_days" in concern 이
        다른 키가 지목된 경우에도 참이 된다 (사유 문장 안에 그 이름이 있으니까)
      → 매입의 반례 검사가 엉뚱하게 통과했다
    ```

    문장을 파싱하지 마십시오. 머리말 따옴표를 정규식으로 여는 것도 결국
    "마스터가 문구를 안 바꾼다" 에 기대는 것이라, 제가 문구를 다듬는 날 다시
    깨집니다. 이 함수를 부르면 기댈 것이 없습니다.

    ```python
    from app.master.domain.verifier import unresolved_supplied_keys

    keys = unresolved_supplied_keys(scenarios, constraints)
    assert "operational_limit_days" in keys
    assert "cap_by_date" not in keys      # 반례가 진짜로 반례가 된다
    ```

    사람이 읽을 문장이 필요하면 `supplied_but_unresolved()` 를 쓴다. 둘은 같은
    검사를 부르므로 갈릴 자리가 없다.
    """
    pairs = _unresolved_pairs(tuple(scenarios), constraints)
    return [key for key, _ in pairs]


def _scenarios_of(proposal: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = proposal.get("scenarios", ())
    if isinstance(raw, Mapping) or not isinstance(raw, Sequence):
        return ()
    return tuple(item for item in raw if isinstance(item, Mapping))


def _rows(value: Any) -> tuple[Mapping[str, Any], ...] | None:
    """매핑들의 배열인가. 아니면 `None` — 빈 배열과 구분한다.

    빈 배열로 접으면 "항목이 없다" 와 "키가 없다" 가 같아 보인다. 앞은 계약 위반이고
    뒤는 아직 안 실린 것이라 처리가 다르다.
    """
    if value is None or isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence):
        return None
    return tuple(item for item in value if isinstance(item, Mapping))


def _int_of(value: Any) -> int | None:
    """정수로 읽는다. `bool` 은 배제한다 — `True` 가 `1` 로 새면 검사가 조용히 통과한다."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _sum_field(rows: Any, field: str) -> int | None:
    """항목들의 한 필드를 더한다. 하나라도 못 읽으면 `None` — 부분합은 대조에 못 쓴다."""
    items = _rows(rows)
    if items is None:
        return None
    total = 0
    for item in items:
        value = _int_of(item.get(field))
        if value is None:
            return None
        total += value
    return total


def _sum_product(rows: Any, left: str, right: str) -> int | None:
    items = _rows(rows)
    if items is None:
        return None
    total = 0
    for item in items:
        a, b = _int_of(item.get(left)), _int_of(item.get(right))
        if a is None or b is None:
            return None
        total += a * b
    return total


def _day_gap(start: Any, end: Any) -> int | None:
    """`YYYY-MM-DD` 두 개의 일수 차이. calendar day 다 — 영업일 보정 없음 (N5)."""
    try:
        return (date.fromisoformat(str(end)) - date.fromisoformat(str(start))).days
    except (ValueError, TypeError):
        return None


# 매입이 밝힌 판정 필드 (2026-08-27 회신). 없으면 그 검사는 skipped 다.
_ALLOWED_AXES = "allowed_axes"
_SPLIT_PLAN = "split_plan"
_TIMING = "timing"

# 분할 매입 지급 일정 — 매입 §3.2 제안 · 재무 회신으로 필드 확정 (2026-08-27)
_PAYMENT_SCHEDULE = "payment_schedule"
_SOURCING_PLAN = "sourcing_plan"


# ── ④ 실행 계획 온전성 (M-16 · §3.7.4) ──────────────────────
#
# 마스터가 호출 순서를 스스로 정하므로 필요한 검사다. 고정 순서 파이프라인이라면
# 코드가 순서를 보장한다.


def _check_plan_integrity(
    plan: ExecutionPlan,
    scenarios: Sequence[Mapping[str, Any]],
    required_advisors: tuple[AgentName, ...],
    out: list[str],
    concerns: list[str],
) -> None:
    """여기서 나오는 것은 대부분 `concerns` 다.

    M-16 이 잡는 것은 마스터 자신의 배선 문제다. 매입을 다시 불러도 마스터가
    물류를 안 부른 사실은 바뀌지 않는다. 사람이 봐야 할 것이지 재시도할 것이 아니다.
    """
    # 필요한 조언자를 다 불렀나 — 하나라도 빠지면 그 부서의 상한이 무한대로 남는다
    for agent in required_advisors:
        if not plan.called(agent, "PRE_PURCHASE"):
            concerns.append(f"M16-AGENT-MISSING: {agent} 를 PRE_PURCHASE 로 부르지 않았다")

    # 순서 역전 — 경계를 받기 전에 시나리오를 만들면 제약 없는 안이 나온다
    first_purchase = next(
        (s.seq for s in plan.steps if s.agent == "purchase" and s.mode == "GENERATE_SCENARIOS"),
        None,
    )
    if first_purchase is not None:
        for agent in required_advisors:
            pre = next(
                (s.seq for s in plan.steps if s.agent == agent and s.mode == "PRE_PURCHASE"),
                None,
            )
            if pre is None or pre > first_purchase:
                concerns.append(
                    f"M16-ORDER: 매입 호출(#{first_purchase})이 {agent} 경계보다 앞섰다"
                )

    # 시나리오가 있는데 판정을 안 받았나
    if scenarios:
        for agent in required_advisors:
            if not plan.called(agent, "SCENARIO_VALIDATION"):
                concerns.append(f"M16-VALIDATION-MISSING: {agent} 가 시나리오를 보지 않았다")

    # 봉투 검증이 남긴 것 — 여기서 다시 계산하지 않고 남아 있는지만 본다.
    #
    # 누구의 위반인지로 갈린다.
    # 매입 것이면 다시 만들면 달라질 수 있으므로 재호출 대상이고,
    # 조언자 것이면 매입을 몇 번 불러도 그대로다 — 보고만 한다.
    seen: set[str] = set()
    for step in plan.steps:
        for code in step.finding_codes:
            line = f"M16-ENVELOPE: {step.agent}/{step.mode} 에 {code}"
            if line in seen:
                continue  # 재호출로 같은 줄이 반복되면 읽는 사람만 피곤하다
            seen.add(line)
            (out if step.agent == "purchase" else concerns).append(line)


#: 조언자가 실제로 낸 판정. 이 셋이 아니면 판정을 안 낸 것이다.
#: `skipped` 는 판정이 아니라 "판정하지 않았다" 는 말이다.
_REAL_VERDICTS = frozenset({"ok", "conditional", "reject"})


def _check_advisor_answered(
    verdicts: Mapping[AgentName, Mapping[str, Any]],
    concerns: list[str],
) -> None:
    """판정을 못 낸 조언자가 화면에서 사라지지 않게 한다.

    `plan.called()` 는 "불렀나" 만 본다 — 답을 못 받아도 통과한다. 그러면 "물어보지
    않았다" 와 "물어봤는데 못 답했다" 가 화면에서 같아 보인다. 앞엣것은 마스터 배선
    문제고 뒤엣것은 그 부서 문제라 완전히 다른 얘기다 (실측 2026-08-31: 물류가 기준일
    불일치를 fail-closed 로 막으며 `business_status=skipped` 를 냈을 때).

    지금 물류가 내는 값은 `RUNTIME_NOT_READY` 다 (2026-09-04 물류 변경).
    `ERROR` 는 "실행이 실패했다" 라 다시 불러 볼 가치가 있고
    (`runner.retryable`), `RUNTIME_NOT_READY` 는 "입력이 없어서 못 낸 답이다"
    라 다시 불러도 같다. 기준일 불일치는 뒤엣것이라 물류가 바꿨고 마스터도
    동의한다. 같이 `missing_data = ("proposal.meta.as_of",)` 가 붙는다.

    아래 분기는 두 값 모두에서 참이다 — `RUNTIME_NOT_READY` 도 `READY` 가 아니고
    `business_status` 도 여전히 `skipped` 다.

    ```python
    if runtime != "READY" or status in ("", "skipped")
    ```

    여기서 `ERROR` 만 따로 알아보게 적지 않는다. 이 함수가 재는 것은
    "판정을 냈나" 뿐이고, 못 낸 이유를 나누는 것은 부서 어휘의 일이다.
    런타임 값 하나하나를 여기 적어 두면 부서가 값을 늘릴 때마다 이 자리가
    따라 낡는다.

    `findings` 가 아니라 `concerns` 다. 매입을 다시 불러도 안 고쳐진다 —
    제안 기준일을 맞추거나 그 부서를 고쳐야 하는 일이라 사람이 봐야 한다.

    이유를 같이 적는다. `reasoning` 이 없으면 이 줄은 "못 답했다" 까지만
    말하고 왜인지는 아무 데도 안 남는다 — 그러면 읽는 사람이 할 수 있는 것이 없다.
    """
    for agent, verdict in verdicts.items():
        status = str(verdict.get("business_status") or "")
        if status in _REAL_VERDICTS:
            continue
        runtime = str(verdict.get("runtime_status") or "?")
        why = str(verdict.get("reasoning") or "").strip()
        # 안 낸 것과 모르는 값을 낸 것을 갈라 적는다. 뒤엣것을 "안 냈다" 고
        # 쓰면 부서가 안 한 일을 했다고 하는 것이고, 고칠 곳도 서로 다르다 —
        # 앞은 그 부서(또는 제안)의 문제, 뒤는 마스터의 어휘가 낡은 것이다.
        what = (
            "시나리오 판정을 내지 않았다"
            if runtime != "READY" or status in ("", "skipped")
            else f"마스터가 모르는 판정값 '{status}' 를 냈다 — 어휘 확인이 필요하다"
        )
        concerns.append(
            f"ADVISOR-NO-VERDICT: {agent} 가 {what} "
            f"(business={status or '없음'} · runtime={runtime}) — "
            f"{why or '사유 미기재'}"
        )


# ── ③ 합쳤을 때의 모순 (§3.7.3) ─────────────────────────────


def _check_timing_gate(
    proposal: Mapping[str, Any],
    scenarios: Sequence[Mapping[str, Any]],
    out: list[str],
    skipped: list[str],
) -> None:
    """타이밍 축이 닫혔는데 분할이 있나.

    ```text
    "timing" ∉ allowed_axes  AND  ∃ s: len(s.split_plan) > 1
    ```

    `allowed_axes` 는 제안 최상위, `split_plan` 은 시나리오 안이다 (매입 스키마 확인
    2026-08-27). 둘 다 시나리오에서 찾으면 `allowed_axes` 가 거기 없어 검사가 영영
    발화하지 않는다. 그런 검사는 `skipped` 로도 안 잡힌다 — "봤는데 문제없음"으로
    읽힌다.

    `strategy_type` 으로 판정하지 않는다 (매입 지정). 축이 하나뿐인 날은 전 안이
    같은 축을 쓰므로 `strategy_type == "timing"` 이 안 나와도 분할은 존재할 수 있다.

    `split_plan` 은 최소 1이다 (매입 스키마). 분할 미적용이 빈 배열이 아니라
    1회차 목록이므로 경계는 `> 1` 이다.
    """
    axes = proposal.get(_ALLOWED_AXES)
    if axes is None:
        if scenarios:
            skipped.append(f"L-TIMING-GATE: 제안에 {_ALLOWED_AXES} 가 없어 미검사")
        return
    if _TIMING in axes:
        return  # 축이 열려 있으면 분할은 정상이다

    for idx, scenario in enumerate(scenarios):
        split = scenario.get(_SPLIT_PLAN)
        if not isinstance(split, Sequence) or isinstance(split, (str, bytes)):
            skipped.append(f"L-TIMING-GATE: scenarios[{idx}] 에 {_SPLIT_PLAN} 이 없어 미검사")
            continue
        if len(split) > 1:
            out.append(
                f"L-TIMING-GATE: scenarios[{idx}] 는 timing 축이 닫혔는데 "
                f"분할 {len(split)} 회차다 (allowed_axes={list(axes)})"
            )


# ── ② 마스터 계산 재검산 (§3.7.3-②) ─────────────────────────
#
# 매입이 §4-2 에서 명시한 항등식을 대조한다. "무엇과 무엇이 같아야 하는가" 가
# 계약에 있어야 재검산할 대상이 생긴다.


def _check_scenario_identities(
    scenarios: Sequence[Mapping[str, Any]],
    out: list[str],
    skipped: list[str],
) -> None:
    """시나리오 층 항등식.

    ```text
    total_qty_kg     == Σ split_plan[].qty_kg == Σ sourcing_plan[].qty_kg
    total_amount_krw == Σ(sourcing_plan[].qty_kg × grade_unit_price)
    ```

    자기검증이 아니다. 매입도 같은 항등식을 강제한다고 했지만, 그 말을 믿는
    것과 확인하는 것은 다르다. 여기서는 원시 항목에서 독립적으로 다시 더해
    매입이 낸 합계와 대조한다 (§3.7.3-②).
    """
    for idx, scenario in enumerate(scenarios):
        label = f"scenarios[{idx}]"
        total_qty = _int_of(scenario.get("total_qty_kg"))
        total_amount = _int_of(scenario.get("total_amount_krw"))

        split_qty = _sum_field(scenario.get(_SPLIT_PLAN), "qty_kg")
        source_qty = _sum_field(scenario.get(_SOURCING_PLAN), "qty_kg")
        source_amount = _sum_product(scenario.get(_SOURCING_PLAN), "qty_kg", "grade_unit_price")

        for name, got, expected in (
            (f"{_SPLIT_PLAN} 수량 합", split_qty, total_qty),
            (f"{_SOURCING_PLAN} 수량 합", source_qty, total_qty),
        ):
            if got is None or expected is None:
                skipped.append(f"L-IDENTITY-QTY: {label} 의 {name} 을 셀 수 없어 미검사")
            elif got != expected:
                out.append(f"L-IDENTITY-QTY: {label} 의 {name} {got:,} ≠ total_qty_kg {expected:,}")

        if source_amount is None or total_amount is None:
            skipped.append(f"L-IDENTITY-AMOUNT: {label} 의 등급별 금액을 셀 수 없어 미검사")
        elif source_amount != total_amount:
            out.append(
                f"L-IDENTITY-AMOUNT: {label} 의 Σ(수량×등급단가) {source_amount:,} "
                f"≠ total_amount_krw {total_amount:,}"
            )


# ── ③ 분할 지급 일정 (매입 §3.2 · 재무 확정) ────────────────


def _check_payment_schedule(
    scenarios: Sequence[Mapping[str, Any]],
    constraints: Mapping[AgentName, Mapping[str, Any]],
    out: list[str],
    skipped: list[str],
) -> None:
    """분할 회차의 지급 일정이 시나리오와 맞물리는가.

    매입이 검증 대상 항등식 5개를 명시했다(§3.2). 그대로 옮긴다.

    ```text
    ① Σ qty_kg          == total_qty_kg
    ② Σ amount_krw      == total_amount_krw
    ③ purchase_date     == split_plan[i].date   (seq 대응)
    ④ payment_date      == purchase_date + N5
    ⑤ 분할이 아닌 시나리오에는 이 키가 없다
    ```

    N5 를 상수로 박지 않는다. 재무 payload 의 `purchase_payment_days` 를 쓰고,
    없으면 ④를 `skipped` 로 남긴다. 7 을 박아 두면 정책이 바뀌어도 검사가
    옛 값으로 통과시킨다 — 검사가 거짓말하는 가장 흔한 방식이다.

    ④는 H1 확정 `payment_date` 가 있으면 건너뛴다 (재무: "H1 값이
    authoritative"). 지금 경로에는 H1 이 없지만, 붙었을 때 이 검사가 정상
    동작을 오류로 잡지 않게 미리 갈라 둔다.
    """
    pay_days = _int_of(constraints.get("finance", {}).get("purchase_payment_days"))

    for idx, scenario in enumerate(scenarios):
        label = f"scenarios[{idx}]"
        split = _rows(scenario.get(_SPLIT_PLAN))
        schedule = _rows(scenario.get(_PAYMENT_SCHEDULE))

        # ⑤ 분할이 아닌 안에는 이 키가 없어야 한다
        if schedule is not None and split is not None and len(split) <= 1:
            out.append(
                f"L-PAYSCHED-UNEXPECTED: {label} 은 분할이 아닌데 "
                f"{_PAYMENT_SCHEDULE} 가 있다 (split {len(split)} 회차)"
            )
            continue

        if schedule is None:
            if split is not None and len(split) > 1:
                # 아직 안 실려 오는 신설 필드다. 통과로 치지 않는다.
                skipped.append(
                    f"L-PAYSCHED: {label} 은 분할 {len(split)} 회차인데 "
                    f"{_PAYMENT_SCHEDULE} 가 없어 미검사"
                )
            continue

        _payment_rows(label, scenario, split, schedule, pay_days, out, skipped)


def _payment_rows(
    label: str,
    scenario: Mapping[str, Any],
    split: Sequence[Mapping[str, Any]] | None,
    schedule: Sequence[Mapping[str, Any]],
    pay_days: int | None,
    out: list[str],
    skipped: list[str],
) -> None:
    # ① 수량 합
    qty = _sum_field(schedule, "qty_kg")
    total_qty = _int_of(scenario.get("total_qty_kg"))
    if qty is not None and total_qty is not None and qty != total_qty:
        out.append(f"L-PAYSCHED-QTY: {label} 의 회차 수량 합 {qty:,} ≠ total_qty_kg {total_qty:,}")

    # ② 금액 합 — 회차 금액은 오늘 단가 기준 추정이지만 합은 총액과 같아야 한다
    amount = _sum_field(schedule, "amount_krw")
    total_amount = _int_of(scenario.get("total_amount_krw"))
    if amount is not None and total_amount is not None and amount != total_amount:
        out.append(
            f"L-PAYSCHED-AMOUNT: {label} 의 회차 금액 합 {amount:,} "
            f"≠ total_amount_krw {total_amount:,}"
        )

    # ③ 매입일이 split_plan 과 seq 대응하는가
    if split is None:
        skipped.append(f"L-PAYSCHED-DATE: {label} 에 {_SPLIT_PLAN} 이 없어 대조 불가")
    elif len(split) != len(schedule):
        out.append(
            f"L-PAYSCHED-DATE: {label} 의 회차 수가 다르다 — "
            f"{_SPLIT_PLAN} {len(split)} vs {_PAYMENT_SCHEDULE} {len(schedule)}"
        )
    else:
        for row, plan_row in zip(schedule, split, strict=True):
            seq = row.get("seq")
            if row.get("purchase_date") != plan_row.get("date"):
                out.append(
                    f"L-PAYSCHED-DATE: {label} seq {seq} 의 purchase_date "
                    f"{row.get('purchase_date')} ≠ {_SPLIT_PLAN} 의 {plan_row.get('date')}"
                )

    # ④ 지급일 = 매입일 + N5
    for row in schedule:
        seq = row.get("seq")
        if row.get("h1_payment_date") or row.get("payment_date_authoritative"):
            skipped.append(f"L-PAYSCHED-N5: {label} seq {seq} 는 H1 확정 지급일이라 미검사")
            continue
        if pay_days is None:
            skipped.append(
                f"L-PAYSCHED-N5: {label} seq {seq} — 재무 purchase_payment_days 가 없어 미검사"
            )
            continue
        gap = _day_gap(row.get("purchase_date"), row.get("payment_date"))
        if gap is None:
            skipped.append(f"L-PAYSCHED-N5: {label} seq {seq} 의 날짜를 읽을 수 없어 미검사")
        elif gap != pay_days:
            out.append(f"L-PAYSCHED-N5: {label} seq {seq} 의 지급 간격 D+{gap} ≠ D+{pay_days}")

    # 상한 — 재무가 STRESS Cashflow 로 쓴다. 수량 × 상한가여야 한다
    max_price = _int_of(scenario.get("max_price"))
    for row in schedule:
        declared = _int_of(row.get("amount_max_krw"))
        row_qty = _int_of(row.get("qty_kg"))
        if declared is None or row_qty is None or max_price is None:
            continue
        if declared != row_qty * max_price:
            out.append(
                f"L-PAYSCHED-MAX: {label} seq {row.get('seq')} 의 amount_max_krw "
                f"{declared:,} ≠ qty {row_qty:,} × max_price {max_price:,}"
            )


# ── 실어 준 값을 미결이라 답하는가 ────────────────────────────

#: 부서가 "이 값이 없어서 못 했다" 고 말할 때 쓰는 말.
_UNRESOLVED_WORDS = ("미확정", "미결", "싣지 않았다", "받지 못")

#: 한 문장 안에서만 본다. 문장이 바뀌면 다른 얘기다.
_SENTENCE_END = re.compile(r"[.。\n;]")

#: 글자 수로 재지 않는다. "cap_by_date 검사는 inbound_lead_days(N4) 미확정으로 보류"
#: 에서 `cap_by_date` 까지 잡힌다고 거리(예: 12자)로 좁히면 원인이 아니라 증상을 잘라
#: 낸 것이 된다 (매입 지적 8/31: "저희가 쓰려던 문구가 12자를 넘어 안 울립니다").
#:
#: 검사가 남의 문장 길이를 정하면 안 된다. 진짜 규칙은 이것이다:
#: 키와 미결 어휘 사이에 다른 실린 키가 끼어 있으면 그 키 얘기다.
#:
#: ```text
#: "cap_by_date 검사는 inbound_lead_days(N4) 미확정으로 보류"
#:   cap_by_date        → 사이에 inbound_lead_days 가 있다        → 건너뛴다
#:   inbound_lead_days  → 사이에 다른 키가 없다                    → 울린다
#:
#: "operational_limit_days는 받았으나 등급 어휘 미확정(#69)"
#:   operational_limit_days → 사이에 다른 키가 없다                → 울린다 (13자여도)
#: ```
#: 키는 이름 전체로만 걸린다. 그냥 부분문자열로 찾으면 짧은 키가 긴 키
#: 안에 걸린다 — 중첩 한 겹을 보므로 `item` 이 `supplied` 에 들어오고, 그러면 아래
#: 문장 하나가 지적 두 줄이 된다 (실측 2026-08-31, 피마늘 관통).
#:
#: ```text
#: "item_storage_policies 반영했으나 결론 미결"
#:   item_storage_policies → 울린다 (맞다)
#:   item                  → item_storage_policies 안에 걸려 또 울린다 (오탐)
#: ```
#:
#: 끼어든 키 규칙으로는 못 막는다. `item` 의 match 는 키 이름 중간에서
#: 끝나므로 뒤에 남는 것이 `_storage_policies …` 다 — 거기엔 `item_storage_policies`
#: 라는 온전한 이름이 없어서 "그 키 얘기다" 로 걸러지지 않는다.
#:
#: 앞뒤가 식별자 글자면 이름의 일부다. 한글 조사(`operational_limit_days는`)는
#: 식별자 글자가 아니라 그대로 걸린다.
_WORD_CHAR = "A-Za-z0-9_"


def _name_pattern(key: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![{_WORD_CHAR}]){re.escape(key)}(?![{_WORD_CHAR}])")


def _name_in(text: str, key: str) -> bool:
    return _name_pattern(key).search(text) is not None


def _unresolved_here(text: str, key: str, others: Iterable[str]) -> bool:
    for match in _name_pattern(key).finditer(text):
        tail = text[match.end() :]
        stop = _SENTENCE_END.search(tail)
        clause = tail[: stop.start()] if stop else tail
        spots = [clause.find(word) for word in _UNRESOLVED_WORDS]
        spots = [i for i in spots if i >= 0]
        if not spots:
            continue
        between = clause[: min(spots)]
        # 끼어든 키도 이름 전체로 본다. `in` 으로 보면 `item` 이
        # `item_storage_policies` 안에 걸려 남의 문장을 가로챈다 — 위와 같은 결함이다.
        if any(other != key and _name_in(between, other) for other in others):
            continue  # 그 키 얘기다 — 같은 원인을 두 번 보고하지 않는다
        return True
    return False


#: 부서가 실은 것이지만 값이 아니라 메타라 대조 대상이 아닌 키.
#:
#: 계약 쪽 목록을 그대로 읽는다 (`envelope.ENVELOPE_META_KEYS`). 같은 목록을 여기
#: 따로 적으면 갈린다 — `required_claims` 는 `soft_warnings` 에 근거를 요구하는데 이
#: 검사는 그것을 메타로 빼는 정반대 상태가 된 적이 있다 (실측 2026-08-30).
_NOT_A_VALUE = ENVELOPE_META_KEYS


def _supplied_keys(constraints: Mapping[AgentName, Mapping[str, Any]]) -> set[str]:
    """봉투에 값이 실린 키. 최상위와 항목 배열 안 한 겹까지 본다.

    한 겹을 보는 이유 (실측 2026-08-31): 매입이 읽는
    `operational_limit_days`·`medium_grade_factor` 는 최상위가 아니라
    `item_storage_policies[]` 안에 있다. 최상위만 보면 `supplied` 에
    `item_storage_policies` 만 들어가고, 매입이 "operational_limit_days 미확정"
    이라고 적어도 이 검사는 조용하다.

    `None` 인 칸은 안 넣는다. 로트 `grade` 가 그렇다 — 전부 `None` 이라
    "실어 준 값" 이 아니고, 매입이 "grade 미확정" 이라 말하면 그건
    맞는 말이다. 맞는 말을 지적으로 올리면 안 된다.

    한 겹만 판다. 더 깊이는 `required_claims` 와 같은 규율이다 —
    더 깊은 중첩의 규칙은 도메인이 정한다.
    """
    supplied: set[str] = set()
    for payload in constraints.values():
        for key, value in payload.items():
            if key in _NOT_A_VALUE or value is None:
                continue
            supplied.add(key)
            if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence):
                continue
            for item in value:
                if not isinstance(item, Mapping):
                    continue
                supplied.update(sub for sub, sub_value in item.items() if sub_value is not None)
    return supplied


def _check_supplied_but_unused(
    scenarios: tuple[Mapping[str, Any], ...],
    constraints: Mapping[AgentName, Mapping[str, Any]],
    concerns: list[str],
) -> None:
    """마스터가 실어 준 값을 부서가 "없다" 고 답하는가.

    조정자만 볼 수 있는 종류다. 각 부서는 자기 쪽만 본다 — 물류는 자기가
    보낸 것을 알고, 매입은 자기가 못 읽은 것을 안다. 둘을 나란히 놓는 것은
    마스터뿐이고, 안 보면 아무도 안 본다.

    실측에서 나왔다 (2026-08-29). `inbound_lead_days` 가 봉투에 `2.0` 으로
    실려 있는데 매입은 "inbound_lead_days(N4) 미확정" 으로 도착일 계산을
    보류했다 — 매입이 봉투 대신 자기 `constraints.yaml` 의 `pending` 을 본다.
    값이 있는데 안 쓰면 더 보수적인 안이 나오고, 아무도 이유를 모른다.

    `findings` 가 아니라 `concerns` 다. 재호출해도 안 고쳐진다 — 배선을
    고쳐야 하는 일이라 사람이 봐야 한다 (§3.4).

    증상을 잡지 원인을 못 찾는다. 실측 ①(2026-08-29)의 원인이 둘이었는데
    이 검사는 하나만 잡았다.

    ```text
    allocate_sourcing 이 pending 을 직접 읽어 pending_value() 우회   ← 잡았다
    어댑터가 N4 를 State 최상위에 안 올려 draft_plan 이 못 봄        ← 못 잡았다
    ```

      뒤엣것은 올바른 함수를 쓰는데도 값을 못 보는 상태다. 봉투 밖에서 값이
      어디로 흐르는지는 그 파트만 알고, 마스터는 "미결이라 답했다" 는 사실까지만
      본다. 원인 규명은 그 파트 몫이고, 이 검사는 물어볼 거리를 만들 뿐이다.

    0건은 키가 실제로 봉투에 있을 때만 통과다. 실리지 않은 키로 낸 0 은 통과가
    아니다.

    원인이 등급 어휘일 수도 있다. 매입이 `operational_limit_days` ·
    `medium_grade_factor` 를 등급 배분에 쓰면(②③), 두 값을 쓰면서도 결론이 안 날 수
    있다 — 로트 `grade` 가 전부 `None` 이면 기준 등급 로트를 특정할 수 없기
    때문이다(#69). 그때 이 검사가 울리는 것은 옳지만, 원인은 물류도 매입도 아니고
    등급 어휘 미확정이다. 그래서 고지 문구가 원인을 하나로 단정하지 않는다.

    키 이름으로만 대조한다. 이름이 다른 불일치는 여기서 못 잡는다
    (물류 `item_storage_policies[].operational_limit_days` vs 매입
    `lots[].shelf_life_days`). 별칭 표를 두면 어긋날 자리가 하나 더 생긴다 — 이름
    합의는 팀이 할 일이다.
    """
    for key, text in _unresolved_pairs(scenarios, constraints):
        concerns.append(
            f"SUPPLIED-BUT-UNRESOLVED: '{key}' 는 봉투에 실려 있는데 "
            f"매입이 미결로 답했다 — 원인은 최소 셋이고 마스터는 "
            f"고르지 않는다: ① 봉투 대신 다른 곳을 본다 ② 올바른 "
            f"함수를 쓰는데 값이 그 자리까지 안 온다 ③ 이 값은 "
            f"쓰는데 다른 입력이 없어 결론이 안 난다 "
            f"(사유: {text[:80]})"
        )


def _unresolved_pairs(
    scenarios: tuple[Mapping[str, Any], ...],
    constraints: Mapping[AgentName, Mapping[str, Any]],
) -> list[tuple[str, str]]:
    """지목된 `(키, 사유 문장)`. 문장을 만들기 전의 사실이다."""
    supplied = _supplied_keys(constraints)
    if not supplied:
        return []

    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for scenario in scenarios:
        for risk in scenario.get("risks") or ():
            text = str(risk)
            for key in supplied:
                if key in seen:
                    continue
                if _unresolved_here(text, key, supplied):
                    seen.add(key)
                    out.append((key, text))
    return out
