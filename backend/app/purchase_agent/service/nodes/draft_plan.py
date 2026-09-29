"""③ draft_plan — 커버일수 D 기반 수량 초안 (상세설계 §4-③).

수량은 전부 계산이 소유한다 (규칙 6). LLM 몫은 "하드 제약 안에서 어떤 조합이 나은가"라는
트레이드오프 판단이며 Epic 3에서 붙는다 — 그때도 아래 클립 결과를 **입력**으로 받는다.

🟢 **자리 (2026-09-29 · 재구성 BL-016).** 전에는 `nodes/draft_plan.py` 였다. 노드 함수만 남기고,
  판정 · 계산은 `domain/draft_plan.py` 로 옮겼다.
"""

from typing import Any

from app.purchase_agent.config import load_constraints
from app.purchase_agent.domain.classify_situation import coverage_by_label, estimate_daily_demand
from app.purchase_agent.domain.draft_plan import (
    ADJUSTMENT_CAP_NAME,
    adjustment_cap_kg,
    cash_cap_kg,
    collect_missing_information,
    deferred_checks,
    draft_one,
    free_stock_for,
    freshness_cap_kg,
    no_quote_plan,
    purchase_budget_krw,
    reference_unit_price,
    split_adjustments,
    warehouse_cap_by_label,
)
from app.purchase_agent.domain.information_requests import to_requests
from app.purchase_agent.domain.quotes import quote_block_reason
from app.purchase_agent.schemas.state import PurchaseAgentState


def draft_plan(state: PurchaseAgentState) -> dict[str, Any]:
    """안별 수량 초안을 만든다.

    ``수량 = round(일평균 확정수요 × 커버일수 D) − 차감보유`` 를 계산하고 하드 제약으로
    클립한다. uncertain이면 공격(D=12)을 아예 만들지 않는다 (§4-③ · 규칙 4).

    🔴 **차감이 식 안에 있다** (`#584` · 2026-09-11). 전에 이 줄이 *"수량 = 확정수요 × D"*
      였고, 그 문장이 ``adapter`` 의 필드 설명으로도 나가 **마스터가 일수요를 절반으로
      되잡았다**. 원수요가 필요하면 ``demand_qty_kg`` 이고 ``total_qty_kg`` 가 아니다.
    """
    constraints = load_constraints()
    coverage = constraints["coverage_days"]
    daily_demand = estimate_daily_demand(state["confirmed_orders"], constraints)
    # 구간이 넓은 날엔 공격안을 만들지 않는다 — **그 규칙은 ①과 공유한다**
    # (`coverage_by_label` · `#340`). 전에는 여기서만 걸러서, ①이 축을 열 때는
    # 공격안이 있는 것처럼 D=12 로 재고 있었다.
    labels = list(coverage_by_label(state["situation"], constraints))
    blocked = quote_block_reason(state["market_quotes"], state["item"], state["date"], constraints)
    if blocked:
        return no_quote_plan(state, constraints, daily_demand, labels, blocked)

    reference_grade = constraints["allocation"]["reference_grade"]
    unit_price = reference_unit_price(state["market_quotes"], reference_grade)

    # 🔴 **창고 상한은 안마다 다르다** (2026-09-16 · E3-9 앞단). 커버 창이 안마다 달라
    #   회차 도착일이 달라지고, 그러면 잡을 수 있는 여유도 달라진다.
    warehouse_caps = warehouse_cap_by_label(state, constraints, labels, coverage)
    cash_cap = cash_cap_kg(purchase_budget_krw(state, constraints), unit_price)
    freshness_cap = freshness_cap_kg(state, daily_demand, constraints)
    # 🔴 **조정안 상한은 안마다 다르다** (2026-09-09 · E3-6). 위 셋은 그날 하나인데
    #   조정안은 ``scenario_labels`` 로 «이 안» 을 겨냥한다 — 재무가 상한 2,000만에
    #   기본·공격만 넘겼으면 보수는 안 건드려야 한다.
    usable, _ = split_adjustments(state.get("adjustments"), constraints)
    # 🔴 **차감을 예약 뺀 재고로 한 번 더 누른다** (2026-09-12). ``lots`` 합은 물리 잔량이라
    #   이미 팔린 몫이 섞여 있다 — ``usable_holdings_kg`` docstring 의 「이름이 같아서 못
    #   봤다」 절. 여기서 품목을 거른다 (``absorb_inventory`` 는 ``lots`` 만 거른다).
    free_stock = free_stock_for(state["inventory"], state["item"])

    drafts = [
        draft_one(
            label=label,
            days=coverage["by_label"][label],
            daily_demand=daily_demand,
            caps={
                # 🔴 **리터럴로 둔다.** 마스터가 이 dict 의 키를 AST 로 세어 부서 소유를
                #   잠근다 (``tests/master/test_binding_constraint_ownership.py``) —
                #   상수로 바꾸면 그 검사가 키를 못 찾는다. 값의 정본은
                #   ``WAREHOUSE_CAP_NAME`` 이고 둘은 같은 문자열이다.
                "창고": warehouse_caps[label].cap_kg,
                "현금": cash_cap,
                "신선도": freshness_cap,
                ADJUSTMENT_CAP_NAME: adjustment_cap_kg(usable, label, unit_price),
            },
            # 🔴 ⑥ 이 **되돌릴 때** 쓰는 기준이다 (E3-9 앞단). ``None`` 이면 날짜 축을
            #   못 본 것이고, 그때는 ③ 이 넓히지도 않았으므로 되돌릴 것도 없다.
            single_round_cap_kg=warehouse_caps[label].single_round_kg,
            coverage=coverage,
            # 🔴 **품목이 걸러진 로트다.** ``absorb_inventory`` 가 다른 품목을 이미 뺐다 —
            #   안 거르면 배추 보유로 무 수요를 깎는다.
            lots=state["inventory"].get("lots"),
            # 🔴 ``caps`` 가 **아니다** — 차감 쪽이다 (§4-③-1). 여기 넣으면 ``clipped_by`` 에
            #   실려 ⑥이 *"하드 제약으로 0까지 축소"* 를 낸다. 보유는 천장이 아니라
            #   **필요가 줄어든 것**이고, 그 조항은 이 판에서 안 건드린다.
            free_stock=free_stock,
        )
        for label in labels
    ]
    # 🔴 **판정은 여기 한 번뿐이다.** 아래 두 칸(고지·구조화 요청)이 같은 결과를 편다.
    #   ``deducted`` 는 차감이 **실제로 걸린 날**에만 참이다 — 안 깎인 날에 그 문장을
    #   내면 "없는 일에 사과하는" 고지가 된다.
    missing = collect_missing_information(
        state,
        constraints,
        state["item"],
        deducted=any(draft["deducted_holdings_kg"] > 0 for draft in drafts),
    )

    return {
        "coverage_days": coverage["by_label"]["기본"],  # §3 State는 대표 D 하나를 담는다
        "base_plan": {
            "daily_demand_kg": daily_demand,
            "reference_unit_price": unit_price,
            "drafts": drafts,
            "deferred_checks": deferred_checks(missing, freshness_cap, state["item"]),
            # 🔴 **플래그와 무관하게 늘 만든다.** 끄는 것은 출력에 싣는 자리(⑦)이고,
            #   판정과 고지는 그대로 돈다 — 플래그로 「못 판정했다」를 없애면 규칙 3이
            #   출력 층에서 깨진다.
            "information_requests": to_requests(missing),
        },
    }
