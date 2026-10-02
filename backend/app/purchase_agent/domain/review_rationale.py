"""⑧ review_rationale — 근거 강도 · 검토 신호 · 판단자에게 줄 재료 — 판정 · 계산 (입력 → 출력만).

노드 함수는 `service/nodes/review_rationale.py` 에 있고, 여기 함수들을 순서대로 부른다.
다른 노드 · 어댑터 · 검사가 이 판정을 다시 부르므로 노드 밖에 둔다.
"""

import re
from typing import Any

from app.purchase_agent.domain.package_scenarios import PAYMENT_CONFLICT_NOTE
from app.purchase_agent.domain.review_gate import ScenarioSignals
from app.purchase_agent.domain.review_templates import FINDINGS
from app.purchase_agent.llm.review_schemas import ClaimIn, ReviewContext
from app.purchase_agent.llm.text_guard import contains_number, sanitize_numerals


def _last_clause(text: str) -> str:
    """마지막 절 — 마침표·줄바꿈으로 자른 뒤 끝 조각.

    문장 전체가 아니라 끝을 본다. 한국어에서 단정·완화는 서술어 끝에 붙고,
    명사(전량·최대)는 문장 어디에나 나오면서 어조를 만들지 않는다.
    """
    조각 = [토막.strip() for 토막 in re.split(r"[.\n]", text) if 토막.strip()]
    return 조각[-1] if 조각 else ""


def claim_strength(text: str, constraints: dict) -> str:
    """문장의 어조. 마지막 절의 어미로 가른다 — 뜻을 재는 것이 아니다.

    부분일치로 가르지 않는다. 문장 어디든 「전량·최대·확실·반드시·이다」가 있으면
    ``ASSERTIVE`` 로 보면 규칙이 쓴 문장이 통째로 오탐이 된다::

        "날짜별 입고 여유 <AMT> (전량 <AMT> 중 <AMT> 이 예정)"

    여기 「전량」은 어조가 아니라 «총량» 이라는 명사다. 부분일치로 갈랐을 때 저장 기록
    근거 문면 3,748종 중 ``ASSERTIVE`` 373건이 전부 이 한 단어 때문이었고, 안 6,295개 중
    5,350개(85%)가 그 오탐을 달고 ⑧ 에게 갔다.

    그것이 나쁜 이유: 지시문이 "claim_strength 가 ASSERTIVE 인데 evidence_strength 가
    ASSUMED 면 결론이 근거보다 세다" 로 판단자를 몬다. 어조가 아닌 것을 어조라고 적어
    주면 판단자는 없는 위반을 찾게 된다.

    어미 목록은 ``constraints.yaml`` 이 소유한다 (규칙 7) — 언어 규칙이라도 값을 코드에
    박으면 바꿀 때 어디를 고치는지가 두 곳이 된다. 그 선언은 부르는 쪽(⑧ 노드)이 읽어
    ``constraints`` 로 넘긴다 — 여기서 파일을 읽지 않는다.

    ``HEDGED`` 를 먼저 본다. 완화 표현이 있으면 단정이 아니다.
    """
    끝 = _last_clause(text)
    어미 = constraints["review"]["claim_strength_endings"]
    if any(끝.endswith(말) for 말 in 어미["hedged"]):
        return "HEDGED"
    if any(끝.endswith(말) for 말 in 어미["assertive"]):
        return "ASSERTIVE"
    return "NEUTRAL"


def scenario_signals(scenario: dict, drafts: list[dict], mix_applied: bool) -> ScenarioSignals:
    """사전검사 신호를 구조에서 뽑는다.

    문장 찾기로 재는 것은 지급 집중일 하나뿐이고, 그 문면은 ⑥ 이 상수로 들고 있다
    (``PAYMENT_CONFLICT_NOTE``) — 같은 말을 두 곳에 적으면 한쪽만 바뀐다.
    """
    라벨 = scenario["label"]
    깎였나 = any(
        draft["label"] == 라벨 and draft.get("clipped_by") for draft in drafts
    )
    risks = scenario.get("risks") or []
    return ScenarioSignals(
        label=라벨,
        mix_applied=mix_applied,
        quantity_clipped=깎였나,
        payment_conflict=any(PAYMENT_CONFLICT_NOTE in 줄 for 줄 in risks),
        # timing 라벨인데 회차가 하나 — 라벨과 실체가 다르다
        label_body_mismatch=(
            scenario.get("strategy_type") == "timing"
            and len(scenario.get("split_plan") or []) <= 1
        ),
        only_deferred_risks=bool(risks)
        and all(_보류류(줄) for 줄 in risks),
    )


def _보류류(문장: str) -> bool:
    """「못 판정했다」만 적힌 문장인가 — 검토할 판단이 없다는 뜻이다."""
    return any(말 in 문장 for 말 in ("보류", "읽지 못", "미확정", "못 받"))


def mix_reason_for_review(mix: Any) -> str | None:
    """⑤ 가 등급 조합을 고른 사유를 정제해서 넘긴다. 안 돌았으면 ``None`` 이다.

    ⑤ 가 실제로 적용된 날만 넘긴다 (``applied``). 규칙 기본안으로 떨어진 날의
    사유는 판단자가 쓴 문장이 아니라 코드가 박은 상수다 — 그것을 넘기면 판단자가
    «자기가 고른 사유» 로 읽고, 안 한 판단을 검토하게 된다.

    가린 뒤에도 숫자가 남으면 안 넘긴다. 못 가린 것을 넣느니 안 본다 — 근거 문장을
    다루는 규율(``build_context``)과 같다.

    빈 문자열로 안 적는다 (규칙 3 의 문자열 판). 「안 돌았다」와 「사유가 비었다」는
    다른 사실이고, 계약이 그 둘을 ``None`` 과 ``str`` 로 가른다.

    그날 판단은 하나다. ⑤ 는 그날 한 번 돌고 ⑥ 이 같은 등급 비율을 모든 안에
    곱한다. 그래서 이 사유는 안마다 다르지 않고, 안 루프 밖에서 한 번 만든다.
    """
    if mix is None or not getattr(mix, "applied", False):
        return None
    가린 = sanitize_numerals(mix.reason or "")
    if not 가린.strip() or contains_number(가린):
        return None
    return 가린


def build_context(
    scenario: dict,
    signals: ScenarioSignals,
    constraints: dict,
    *,
    mix_reason: str | None = None,
    mix_labels: tuple[str, ...] = (),
) -> ReviewContext:
    """검토 재료. 숫자·날짜를 가려서 넣는다.

    가리기 전 원문이 새면 판단자가 그 값을 지적에 베껴 쓴다. 넣기 직전에 다시
    확인하고, 남아 있으면 그 근거를 아예 안 넣는다 — 못 가린 것을 넣느니 안 본다.

    ``mix_reason`` · ``mix_labels`` 는 그날 하나라 부르는 쪽이 만들어 준다
    (``mix_reason_for_review`` · ``llm.mix.context_labels``).
    """
    claims = []
    for 항목 in scenario.get("rationale") or []:
        가린 = sanitize_numerals(항목.get("claim") or "")
        if not 가린.strip() or contains_number(가린):
            continue
        claims.append(
            ClaimIn(
                ref_id=항목["ref_id"],
                evidence_category=항목["source"],
                evidence_strength=항목.get("evidence_grade") or "ASSUMED",
                claim_strength=claim_strength(가린, constraints),
                claim_text=가린,
            )
        )
    return ReviewContext(
        scenario_label=scenario["label"],
        strategy_type=scenario["strategy_type"],
        round_count="MULTI" if len(scenario.get("split_plan") or []) > 1 else "SINGLE",
        claims=claims,
        risk_categories=sorted(
            {_위험범주(줄) for 줄 in (scenario.get("risks") or [])}
        ),
        signals=[이름 for 이름, 켜짐 in _SIGNAL_LABELS(signals) if 켜짐],
        mix_reason=mix_reason,
        mix_labels=list(mix_labels),
        offered_findings=list(FINDINGS),
    )


def _SIGNAL_LABELS(signals: ScenarioSignals) -> list[tuple[str, bool]]:
    return [
        ("MIX_APPLIED", signals.mix_applied),
        ("QUANTITY_CLIPPED", signals.quantity_clipped),
        ("PAYMENT_CONFLICT", signals.payment_conflict),
        ("LABEL_BODY_MISMATCH", signals.label_body_mismatch),
    ]


def _위험범주(문장: str) -> str:
    """위험 문장을 범주 하나로 접는다 — 빠진 것을 물으려면 있는 것을 알아야 한다."""
    for 말, 범주 in (
        ("창고", "WAREHOUSE"),
        ("신선도", "FRESHNESS"),
        ("지급", "CASH"),
        ("등급", "GRADE"),
        ("분할", "SPLIT"),
        ("로트", "LOT_AGE"),
    ):
        if 말 in 문장:
            return 범주
    return "OTHER"
