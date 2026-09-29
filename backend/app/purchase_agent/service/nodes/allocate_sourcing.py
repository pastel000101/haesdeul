"""⑤ allocate_sourcing — 등급 배분 스코어링 (상세설계 §4-⑤ · 백로그 E3-1).

**계산만 한다** (규칙 6). 단가 이득과 신선도 리스크를 견줘 중품을 얼마나 태울지 정하고,
그 판단이 순수 함수 다섯 개로 쪼개져 있다. 조합 트레이드오프의 LLM 판단(E3-2)은 이 결과를
**입력**으로 받는다 — 아래 결과를 다시 계산하지 않는다.

§4-⑤ Epic 3 확정(8/25) 두 가지가 이 파일의 구조를 정했다:

1. **'평시' 기준은 constraints의 선언 상수**다. 과거 시세 이력 포트가 계약에 없기 때문이다.
   대신 판정을 ``baseline_spread()`` 한 함수로 격리해, 실데이터 전환 시 그 함수 본문만
   직전 N일 통계로 바꾸면 되게 했다.
2. **평시 중품 비중 상수는 두지 않는다.** 배분은 스코어링의 출력이다 — 같은 함수가
   입력(스프레드)만으로 평시엔 상품 수렴, 확대일엔 중품 확대로 갈린다.

**출력은 비율이다** (kg이 아니다). 안별 총량이 달라 절대 수량은 ⑥이 만든다. ⑤가 kg을
만들지 않으므로 사중 일치의 수량 축을 여기서 깨뜨릴 수단 자체가 없다.

🟢 **자리 (2026-09-29 · 재구성 BL-016).** 전에는 `nodes/allocate_sourcing.py` 였다. 노드 함수와 ⑤
  등급 조합 판단자를 부르는 도우미(`_select_mix`)만 남기고, 판정 · 계산은
  `domain/allocate_sourcing.py` 로 옮겼다.
"""

from dataclasses import replace
from typing import Any

from app.purchase_agent.config import load_constraints
from app.purchase_agent.domain.allocate_sourcing import (
    PRECEDENCE_DIRECTION,
    apply_mix_precedence,
    base_grade_for,
    build_mix_candidates,
    evaluate_mid_grade,
    mix_signals,
    ratio_line,
    yields_positive_kg,
)
from app.purchase_agent.domain.draft_plan import fixed_market_quotes
from app.purchase_agent.domain.quotes import quote_block_reason
from app.purchase_agent.llm.mix import MixDecision, MixSelector, build_mix_context, shelf_is_tight
from app.purchase_agent.llm.schemas import MIX_PRECEDENCE_SIGNAL, MixCandidate
from app.purchase_agent.schemas.state import PurchaseAgentState


def _select_mix(
    state: PurchaseAgentState,
    facts: dict,
    constraints: dict,
    selector: MixSelector | None,
) -> tuple[float, MixDecision | None]:
    """후보를 만들고 LLM에게 고르게 한다. **숫자는 후보의 것을 그대로 쓴다.**

    기본안(``default``)은 **규칙이 고르던 값**이다 — LLM이 꺼져 있든 전면 실패하든 이
    함수가 돌려주는 비율이 E3-1 시절과 같아진다. 회귀가 아니라 무변화다.
    """
    rule_ratio = facts["ratio"]
    # ``cap_ratio``는 **키 자체가 없을 수 있다** — evaluate_mid_grade가 blocked_by를 세우고
    # 조기 반환하는 경로(상 등급 로트 없음·기준선 미확정 등)에서는 거기까지 가지 않는다.
    # 그때는 중품 배정 자체를 안 하는 날이므로 고를 후보도 없다.
    cap_ratio = facts.get("cap_ratio")
    if cap_ratio is None or selector is None:
        return rule_ratio, None
    # **규칙이 중품을 태우기로 한 날에만 묻는다.** E3-1의 AND 게이트(확대 ∧ 스코어>0)가
    # "중품을 쓸 것인가"를 소유하고, LLM은 "쓴다면 얼마나"만 판단한다.
    #
    # 게이팅을 "후보 ≥ 2"로만 두면 안 된다 — 실측 결과 cap_ratio는 스프레드와 무관하게
    # 근접 납품량으로 계산되므로 **평시에도 후보가 3개** 나오고, 4앵커 × 4품목 = 16회
    # 전부 호출된다(백로그 비용 완화책이 무력화). 여기가 그 계산과 실제가 갈린 자리다.
    #
    # 평시에 LLM에게 문을 열어주면 E3-1의 양파 반례가 부활한다: 양파는 신선도 리스크가
    # 0으로 눌려 스코어만 보면 평시에도 100% 중품이 "합리적"으로 보인다. 그 판단을
    # 막는 게 확대 게이트이고, LLM이 그걸 우회할 수 있으면 게이트가 사라진 것이다.
    if rule_ratio <= 0:
        return rule_ratio, None

    지은_후보 = build_mix_candidates(state, cap_ratio, constraints)
    # 🔴 **규칙이 먼저 좁힌다** (2026-09-15). 라벨 둘이 부딪히는 날의 우열은 업무 판단이라
    #   선언이 소유하고, 판단자는 그 뒤에 남은 것을 «설명» 한다.
    candidates = apply_mix_precedence(
        지은_후보,
        spread_widened=bool(facts.get("widened")),
        shelf_tight=shelf_is_tight(facts),
        cap_ratio=cap_ratio,
        declaration=constraints["grade"]["mix_precedence"],
    )
    좁혔나 = len(candidates) < len(지은_후보)
    by_id = {candidate_id: ratio for candidate_id, ratio, _ in candidates}
    # 규칙이 고르던 비율에 해당하는 후보를 기본안으로 삼는다. 없으면 LLM을 부르지 않는다 —
    # 고를 목록에 기본안이 없으면 실패 시 돌아갈 자리가 사라진다.
    # 정확 비교다. 후보 비율이 ``cap × fraction``이고 규칙이 채택한 값이 ``cap``이므로
    # ``cap × 1.0``이 정확히 일치한다 — 반올림을 끼우면 그 등식이 깨진다.
    #
    # 🔴 **좁힌 날은 기본안도 같이 옮긴다.** 우열은 규칙이지 판단자가 아니므로, LLM 이
    #   꺼졌든 전면 실패했든 **같은 답**이 나와야 한다. ``rule_ratio`` 를 안 옮기면
    #   fallback 이 옛 값으로 되돌아가 «우열표가 있는데 실패하면 무시되는» 자리가 된다.
    if 좁혔나:
        default_id, rule_ratio = candidates[0][0], candidates[0][1]
    else:
        default_id = next((cid for cid, ratio in by_id.items() if ratio == rule_ratio), None)
    # 🔴 **«좁히기 전» 으로 잰다.** 원래 후보가 하나뿐이던 날은 지금 그대로 안 부른다 —
    #   그날은 우열이 한 일이 없고, 부르면 산출물에 없던 줄이 생긴다.
    if default_id is None or len(지은_후보) < 2:
        return rule_ratio, None

    signals, facts_text = mix_signals(facts)
    if 좁혔나:
        # 🔴 **판단자에게 «왜 하나뿐인가» 를 준다.** 안 주면 사유가 라벨과 어긋난 말을
        #   쓰고(⑧ 의 ``MIX_REASON_LABEL_MISMATCH`` 가 그것을 본다), 읽는 사람은
        #   «판단자가 골랐다» 로 읽는다. 숫자는 안 넣는다 (규칙 6).
        signals.append(MIX_PRECEDENCE_SIGNAL)
        이긴_쪽 = PRECEDENCE_DIRECTION[
            str(constraints["grade"]["mix_precedence"]["winner"])
        ][0]
        facts_text.append(
            f"스프레드와 신선도가 부딪혀, 규칙이 정한 우열에 따라 {이긴_쪽} 쪽으로 "
            "중품 비중을 한 가지로 좁혔다."
        )
    context = build_mix_context(
        state["item"],
        spread_widened=bool(facts.get("widened")),
        shelf_days=facts.get("shelf_days"),
        # 🔴 판정은 ``llm/mix`` 가 소유한다 — ⑧ 이 같은 판정을 되읽어야 대조가 성립한다.
        shelf_tight=shelf_is_tight(facts),
        signals=signals,
        facts=facts_text,
        candidates=[
            MixCandidate(candidate_id=cid, summary=summary)
            for cid, _, summary in candidates
        ],
    )
    decision = selector(context, default_id)
    if decision.candidate_id not in by_id:
        # **서비스 검증기만으로는 부족하다.** selector는 주입 가능한 콜러블이라 그 층을
        # 우회할 수 있고, 실제로 우회하면 비율만 규칙값으로 되돌아가고 결정 객체는 그대로
        # 남아 출력에 "없는 후보를 선택함"이라고 기록된다 — 라벨과 행동이 어긋난다
        # (Codex 교차검증 P2, 재현 확인). 노드가 자기 후보 집합으로 한 번 더 확인한다.
        #
        # ``None``으로 지우지 않는다. 그러면 고지까지 사라져 "판단자가 이상한 값을 줘서
        # 되돌렸다"는 사실이 소비자에게 안 보인다 — 조용히 넘기지 않는 게 이 프로젝트의
        # 규칙이다. 실패로 표시해 ⑥이 risks에 싣게 한다.
        return rule_ratio, replace(
            decision,
            candidate_id=default_id,
            reason="규칙 기본안",
            llm_status="FALLBACK",
            llm_fallback_used=True,
        )
    return by_id[decision.candidate_id], decision


def allocate_sourcing(
    state: PurchaseAgentState, *, selector: MixSelector | None = None
) -> dict[str, Any]:
    """등급 배분 비율을 정한다. **계산이 후보를 만들고 LLM은 고르기만 한다** (E3-1 + E3-2).

    ``evaluate_mid_grade``가 낸 사실들(스프레드·소진 한계일·근접 납품량·스코어)로 규칙이
    후보 집합을 만들고, LLM은 그중 **id 하나**를 고른다. 숫자는 계산이 소유한다 (규칙 6 ·
    §4-⑤ E3-2 확정) — LLM 출력 스키마에 비율 필드가 아예 없어 생성이 타입으로 불가능하다.

    ``selector``를 주입 가능하게 둔 이유: 테스트가 결정적이어야 한다. 기본값 ``None``은
    "LLM 없이 규칙만"이고, 그 경로가 E3-1의 산출물과 **완전히 같다**.
    """
    constraints = load_constraints()
    if quote_block_reason(state["market_quotes"], state["item"], state["date"], constraints):
        # 시세를 쓸 수 없는 날(0건이거나 너무 오래됨). ③이 이미 안을 만들지 않았고 사유도
        # 남겼으므로, 여기서는 **배분할 대상이 없다**는 사실만 빈 목록으로 돌려준다.
        # ③과 **같은 판정 함수**를 쓴다 — 두 노드가 각자 판단하면 한쪽만 바뀐다.
        # ``fixed_market_quotes``의 가드는 그대로 둔다 — 그 함수의 뜻은 "빈 값으로 조용히
        # 계산하지 말라"이지 "죽어라"가 아니고, 사유를 낼 수 있는 자리에서 먼저 낸다.
        return {"sourcing_plan": []}
    quotes = fixed_market_quotes(state["market_quotes"])
    prices = {quote["grade"]: quote["price"] for quote in quotes}

    top_grade = constraints["allocation"]["reference_grade"]
    # 기준등급 시세가 없으면 **사다리를 안 내려가되 그중 싼 것**으로 간다 (`#574` ·
    # 근거와 내력은 ``base_grade_for`` docstring). **기준등급이 아니므로** 실제로 배정한
    # 등급을 facts에 남긴다 — ⑥의 risks가 "기준등급으로 배정"이라고 적으면 형식만 맞고
    # 내용이 거짓인 근거가 나간다.
    base_grade = base_grade_for(prices, top_grade, constraints)

    decision = evaluate_mid_grade(state, constraints)
    decision["base_grade"] = base_grade
    if base_grade != top_grade:
        # 🔴 **대체했다는 사실을 따로 남긴다.** ``base_grade`` 만으로는 그것이 선언한
        #   기준등급인지 대체값인지 읽는 쪽이 알 수 없다 — 이름이 같으니 ⑥이 조용히
        #   "기준등급으로 배정" 이라고 적게 된다(바로 위 주석이 막으려던 그 모양이다).
        #
        # ⚠️ **실데이터에서 늘 걸리는 자리다** (2026-09-07 실측 · `#69`)::
        #
        #       배추   특만 있다        → 선언 "상" 이 없어 특으로 간다
        #       양파   특·중·하        → 마찬가지
        #       무     특·상           → 선언대로 상
        #
        #   품목마다 사다리가 달라 상수 교체로 못 없앤다. 없애는 대신 **보이게** 한다.
        decision["reference_grade_fallback"] = {
            "declared": top_grade,
            "used": base_grade,
            "used_price": prices[base_grade],
        }
    mid_ratio, mix = _select_mix(state, decision, constraints, selector)
    decision["ratio"] = mid_ratio
    decision["mix"] = mix

    min_share = constraints["grade"]["min_share"]
    mid_ok = mid_ratio >= min_share and yields_positive_kg(state, mid_ratio)
    # 잔여분도 같은 검사를 받아야 한다. 근접 납품이 확정주문 전부인 날(양파)은
    # 상한이 1.0이 되어 기준등급 줄이 **0이 된다** — 그건 오류가 아니라 "전량 중품"이다.
    base_ok = yields_positive_kg(state, 1.0 - mid_ratio)

    if mid_ok and base_ok:
        # **중품이 먼저다.** ⑥이 마지막 줄에 잔량을 흡수시키므로, 중품을 끝에 두면 반올림
        # 나머지가 신선도 상한을 넘길 수 있다. 상품이 흡수하면 중품은 항상
        # round(총량 × 비율) 이하로 유지된다.
        lines = [
            ratio_line(decision["mid_grade"], prices, mid_ratio),
            # 1.0 - mid_ratio로 **구성**한다. 두 비율을 각자 계산해서 더하면 부동소수점
            # 합이 1에서 밀려 ⑥의 합계 검사(1e-9)에 걸릴 수 있다.
            ratio_line(base_grade, prices, 1.0 - mid_ratio),
        ]
    elif mid_ok:
        decision = {**decision, "ratio": 1.0}
        lines = [ratio_line(decision["mid_grade"], prices, 1.0)]
    else:
        # 비율 0을 **줄로 내보내지 않는다.** ⑥의 _validate_ratios가 ratio > 0을 요구한다.
        # 평시 출력이 스텁 시절과 동일해지는 것도 이 경로다.
        decision = {**decision, "ratio": 0.0}
        lines = [ratio_line(base_grade, prices, 1.0)]

    lines[0] = {**lines[0], "decision": decision}
    return {"sourcing_plan": lines}
