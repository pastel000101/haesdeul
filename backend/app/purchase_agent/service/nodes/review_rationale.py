"""⑧ review_rationale — **근거를 스스로 한 번 더 읽는다** (E3-10).

🔴 **⑦ 뒤에 선다.** 계산 검사가 끝나고 살아남은 안만 본다. ⑦ 안에 섞지 않는 이유는
경계 때문이다 — ⑦ 은 컷 권한이 있고 여기는 없다. 컷하는 함수 안에 컷 못 하는 판단을
두면 나중에 누가 「이것도 컷하면 되지 않나」로 읽는다.

🔴 **수량·분할·등급·금액·날짜·컷 결과를 안 바꾼다.** 더하는 것은 안의 ``risks`` 한두 줄뿐이다.
   ⚠️ 그래서 ``risks`` 와 산출물 해시는 **의도적으로 달라진다** — 「아무것도 안 바뀐다」가
   아니다. ``risks`` 는 ``Scenario`` 의 필드이므로 그 둘이 동시에 참일 수 없다.

🔴 **실패·비활성이면 제안이 그대로 지나간다.** 지적 0건과 **검토를 못 한 것**은 다른
   사실이고, 그 구분은 실행 흔적(``llm_calls``)이 든다.

★ 판단자에게 넣는 문장은 **숫자·날짜를 가린 것**이다. 가리기 전 원문이 새면 판단자가 그
  값을 지적에 베껴 쓰고, 그 순간 규칙 6 이 깨진다 — 노드가 넣기 직전에 다시 확인한다.

🟢 **자리 (2026-09-29 · 재구성 BL-016).** 전에는 `nodes/review_rationale.py` 였다. 노드 함수와 ⑧
  실행 흔적(`_기록`)만 남기고, 판정 · 계산은 `domain/review_rationale.py` 로 옮겼다.
"""

from typing import Any

from app.contracts.envelope import LLMCallMetadata
from app.purchase_agent.config import load_constraints
from app.purchase_agent.domain.review_gate import GateResult, choose
from app.purchase_agent.domain.review_rationale import (
    build_context,
    mix_reason_for_review,
    scenario_signals,
)
from app.purchase_agent.domain.review_templates import UnknownFinding, render
from app.purchase_agent.features import SELF_REVIEW, enabled
from app.purchase_agent.llm.mix import context_labels
from app.purchase_agent.llm.self_review import ROLE, Reviewer
from app.purchase_agent.schemas.proposal import PurchaseProposal, revalidate_for_output
from app.purchase_agent.schemas.state import PurchaseAgentState

#: 주장이 얼마나 센가 — 🔴 **규칙이 어휘로 판정한다.** 판단자에게 매기게 하면 그 판정을
#: 근거로 다시 판정하는 셈이 되어, 무엇을 재는지가 사라진다.
#: ⑧ 의 역할 이름. 🔴 어댑터가 같은 이름을 쓰지만 **그쪽을 import 하면 순환**이다
#: (어댑터가 그래프를 부른다). 문자열을 두 곳에 적는 대신, 계약 검사가 둘이 같은지 잠근다.
RATIONALE_SELF_REVIEW = "rationale_self_review"


def review_rationale(
    state: PurchaseAgentState, *, reviewer: Reviewer | None = None
) -> dict[str, Any]:
    """살아남은 안의 근거를 검토해 **경고만** 더한다.

    🔴 제안을 **다시 계약에 태운다** (``revalidate_for_output``). 리스트에 값을 끼워 넣는
    경로는 어떤 validator 도 안 거치기 때문이고, 그 함수가 바로 그것을 막으려고 이미 있다.
    """
    proposal = state.get("proposal")
    if not proposal or not proposal.get("scenarios"):
        return {}
    if reviewer is None or not enabled(SELF_REVIEW):
        # 🔴 설정이 꺼진 것과 게이트가 안 고른 것은 다른 사실이다 — 안마다 한 줄씩 남긴다.
        return {
            "review_calls": tuple(
                _기록(안["label"], "DISABLED") for 안 in proposal["scenarios"]
            )
        }

    drafts = ((state.get("base_plan") or {}).get("drafts")) or []
    판단 = ((state.get("sourcing_plan") or [{}])[0].get("decision")) or {}
    mix = 판단.get("mix")
    신호 = [
        scenario_signals(안, drafts, bool(mix is not None and mix.applied))
        for 안 in proposal["scenarios"]
    ]
    고른: GateResult = choose(신호)
    기록 = [
        *(_기록(라벨, "SKIPPED_BY_GATE", "사전검사가 대상으로 안 골랐다")
          for 라벨 in 고른.skipped_by_gate),
        *(_기록(라벨, "SKIPPED_BUDGET", "실행당 검토 상한에 걸렸다")
          for 라벨 in 고른.skipped_by_budget),
    ]
    if not 고른.selected:
        return {"review_calls": tuple(기록)}

    # ★ 그날 판단이 하나라 **루프 밖에서** 한 번 만든다 — 안마다 다시 만들면 같은 사유가
    #   안마다 달리 정제될 수 있고, 그러면 같은 판단을 세 번 다르게 검토하게 된다.
    검토용_사유 = mix_reason_for_review(mix)
    # 🔴 **라벨을 같이 넘긴다.** 사유만 주면 「사유가 라벨과 맞나」를 물을 수 없다 —
    #   그 지적이 비교할 대상이 없어진다. 안 돌았으면 빈 목록이다.
    검토용_라벨 = context_labels(판단) if 검토용_사유 is not None else ()
    # 주장 어조를 가르는 어미 목록(부서 선언) — domain 이 파일을 읽지 않으므로 여기서 한 번 읽는다.
    constraints = load_constraints()
    보임 = {s.label: s for s in 신호}
    바뀐 = [dict(안) for 안 in proposal["scenarios"]]
    for 안 in 바뀐:
        if 안["label"] not in 고른.selected:
            continue
        context = build_context(
            안,
            보임[안["label"]],
            constraints,
            mix_reason=검토용_사유,
            mix_labels=검토용_라벨,
        )
        결과 = reviewer(context)
        상태, 사유, 문장 = 결과.llm_status, None, []
        if 상태 == "SUCCESS":
            try:
                문장 = render(
                    [(f.code, f.target_ref_id) for f in 결과.output.findings],
                    {c.ref_id for c in context.claims},
                )
            except UnknownFinding as error:
                # 🔴 **「검토 성공, 문제 없음」으로 안 적는다.** 지적을 버리고 SUCCESS 를
                #   남기면 화면에는 아무 말이 없고 흔적에는 «봤는데 깨끗했다» 가 남는다 —
                #   실제로는 **검토 결과를 적용하지 못한 것**이다.
                #
                # ⚠️ 예외를 그대로 올리지 않는 이유: ⑦ 이 이미 통과시킨 제안이 ⑧ 때문에
                #   통째로 사라진다. 경고만 하는 자리가 산출물을 죽이면 경계가 뒤집힌다.
                #   ★ 검증(``validate_output``)이 이미 막는 자리라 **여기 오면 내부 계약
                #     위반**이고, 그래서 조용히 넘기지 않고 사유를 남긴다.
                상태, 사유, 문장 = "FALLBACK", f"검토 결과를 문장으로 못 옮겼다 — {error}", []
        기록.append(
            _기록(
                안["label"],
                상태,
                사유,
                attempts=결과.llm_attempts,
                fallback_used=결과.llm_fallback_used or 상태 == "FALLBACK",
                provider=결과.llm_provider,
                model=결과.llm_model,
            )
        )
        if 문장:
            안["risks"] = [*(안.get("risks") or []), *문장]

    새제안 = {**proposal, "scenarios": 바뀐}
    return {
        "proposal": revalidate_for_output(
            PurchaseProposal.model_validate(새제안)
        ).model_dump(mode="json"),
        "review_calls": tuple(기록),
    }


def _기록(
    label: str,
    status: str,
    skip_reason: str | None = None,
    *,
    attempts: int = 0,
    fallback_used: bool = False,
    provider: str | None = None,
    model: str | None = None,
) -> LLMCallMetadata:
    """안 하나의 검토 흔적.

    🔴 **안 본 안도 남긴다.** 목록에서 빼면 *"봤는데 깨끗했다"* 와 구분되지 않고,
    검토율이 거짓이 된다.
    """
    # 🔴 **부른 호출에만 판을 적는다.** 안 불렀는데 적으면 *"이 판으로 물어봤다"* 로
    #   읽힌다 — ``LLMCallMetadata`` 가 그 등식을 계약으로 잠근다.
    판 = (
        {"prompt_version": ROLE.prompt_version, "schema_version": ROLE.schema_version}
        if attempts > 0
        else {}
    )
    return LLMCallMetadata(
        role=RATIONALE_SELF_REVIEW,
        status=status,  # type: ignore[arg-type]
        attempts=attempts,
        fallback_used=fallback_used,
        target=label,
        provider=provider or None,
        model=model or None,
        **판,
        skip_reason=skip_reason,
    )
