"""⑥ package_scenarios — 보수/기본/공격 묶기 + 근거 작성 (상세설계 §4-⑥).

숫자는 계산이 소유한다 (규칙 6). **LLM 이 닿는 것은 ``rationale`` 의 ``claim`` 뿐이다**
(``context_rationale`` 주석 참조).

🔴 **``risks`` 는 LLM 이 안 다듬는다** (2026-09-13 정정).

  전 판은 *"Epic 2는 rule_only 경로다. … Epic 3에서 붙일 때도 … ``rationale·risks`` 의
  서술만 손본다"* 였다. **Epic 3 은 닫혔고, 붙은 LLM 은 ``risks`` 를 안 건드린다** —
  그 문장은 **계획**이었는데 계획이 그대로 남아 **서술처럼 읽혔다.**

  ``risks`` 는 순수 함수를 이어 붙인 결과다::

      risks · forecast_risks · adjustment_risks · context_risks
      sourcing_risks · split_risks · payment_risks

  ⚠️ ``sourcing_risks`` 안에서 ``_mix_choice_risks`` 를 부르고, **그 하나만 LLM 상태를
    읽는다** (``mix.applied`` · ``mix.llm_fallback_used``). 다만 문장 하나가 **붙었다
    떨어질** 뿐이고, 다른 문장의 **문면을 바꾸지는 않는다.**

★ **이 머리말이 남을 오도했다.** 마스터가 이 문장을 근거로 *"걷기 요약의 옛 문장 수를
  셀 때 LLM 성공 날은 따로 보겠다"* 고 답했다 (2026-09-13 23:2x). ``risks`` 문자열을
  세는 일은 **LLM 상태와 무관하므로 한 버킷으로 세도 된다.**

  🟢 **그 답은 거둬졌다** (마스터 회신 2026-09-14) — *"제 「LLM 성공 날은 따로
    세겠다」를 거둡니다. 확인 걷기에서 옛 문장·새 문장은 LLM 날과 규칙 날을 한 묶음으로
    셉니다."* ⚠️ 위 ``_mix_choice_risks`` 단서도 같이 받았다: 그 문장은 `#646` 두 문장과
    **다른 고정 문장이라 세기에 안 섞인다.**

  ⇒ 사실이 바뀌면 문장도 따라가야 한다. `#646` 이 같은 병을 화면 문면에서 고쳤다.

  🔴 **그리고 이 자리가 그 병으로 두 번째다.** `#648` 은 «계획이 서술처럼 읽히는» 것을
    고쳤고, 바로 위 🟢 줄이 없던 동안에는 «인용이 아직 살아 있는 답처럼 읽혔다».
    ⇒ **인용을 적을 때는 그 답이 뒤집힌 날도 같이 적는다.** 인용만 남기면 시점이 안 보인다.

🟢 **자리 (2026-09-29 · 재구성 BL-016).** 전에는 `nodes/package_scenarios.py` 였다. 노드 함수만
  남기고, 판정 · 계산은 `domain/package_scenarios.py` 로 옮겼다.
"""

from typing import Any

from app.purchase_agent.config import load_constraints
from app.purchase_agent.domain.allocation import assign_axes
from app.purchase_agent.domain.draft_plan import ADJUSTMENT_CAP_NAME
from app.purchase_agent.domain.guards import pending_value
from app.purchase_agent.domain.package_scenarios import (
    adjustment_risks,
    allocation_choice_rationale,
    allocation_risks,
    compute_cut_unit_price,
    compute_margin,
    compute_max_price,
    context_rationale,
    context_risks,
    forecast_risks,
    materialize_sourcing,
    materialize_split,
    no_quantity_reason,
    payment_risks,
    payment_schedule_field,
    rationale,
    risks,
    sourcing_decision,
    sourcing_rationale,
    sourcing_risks,
    split_rationale,
    split_risks,
    weighted_unit_price,
    with_round_amounts,
)
from app.purchase_agent.domain.quotes import observed_at
from app.purchase_agent.domain.split_outcome import ROLLED_BACK, settle_split
from app.purchase_agent.domain.split_plan import effective_allowed_axes, split_decision
from app.purchase_agent.schemas.proposal import TIMING_AXIS
from app.purchase_agent.schemas.state import PurchaseAgentState


def package_scenarios(state: PurchaseAgentState) -> dict[str, Any]:
    """안별로 split·sourcing을 묶고 근거를 붙여 시나리오를 완성한다."""
    constraints = load_constraints()
    base = state["base_plan"]
    drafts = base["drafts"]
    split_choice = state["split_plan"]  # ④ 분할 유형·비율 (진입 안 했으면 1회차 목록)
    # 🔴 **실효 축으로 배정한다** (`#308`). ①이 연 축 그대로 주면 ④가 안 나눈 날에도
    #   timing 라벨이 서서 «회차 하나짜리 분할안» 이 된다 — ``effective_allowed_axes``
    #   docstring 에 근거와 실측이 있다. ⑦ ``check_axis_diversity`` 도 **같은 목록**을 본다.
    effective_axes = effective_allowed_axes(state["allowed_axes"], split_choice)
    labels = [d["label"] for d in drafts]
    aggressive_axis = constraints["allocation"]["aggressive_axis"]
    axes = assign_axes(labels, effective_axes, aggressive_axis)
    # ①이 연 축 그대로 배정했으면 어느 안이 timing 을 받았을지 — **고지를 붙일 자리**를
    # 짚는 데만 쓴다. 판정에는 안 쓴다 (`split_risks` 의 ``timing_withdrawn``).
    declared_axes = assign_axes(labels, state["allowed_axes"], aggressive_axis)
    lots = state["inventory"].get("lots")
    contract_price = state["contract_price"]  # 미수령이면 None — 마진 두 값이 함께 null이 된다
    decision = sourcing_decision(state["sourcing_plan"])  # ⑤ 등급 배분 판단 근거
    # N4·수용량은 그날 하나뿐이라 안 루프 밖에서 한 번만 읽는다.
    # ``cap_by_date``는 **어댑터 경로에만** 있다 — mock 재고에는 없어서 None이고,
    # 그때 회차 조정은 일어나지 않는다 (부재가 정상 경로다).
    lead_days = pending_value(state, constraints, "inbound_lead_days")
    cap_by_date = state["inventory"].get("cap_by_date")
    # 회차일을 장이 서는 날로 미는 데 쓴다 (`#300` · ``round_offsets``). 그날 하나뿐이라
    # 안 루프 밖에서 한 번 읽는다 — ``lead_days`` · ``cap_by_date`` 와 같은 이유다.
    calendar = state.get("execution_calendar")
    split_facts = split_decision(split_choice)
    # 시세 근거 좌표는 **관측일 기준**이고 그날 하나뿐이다. 안 루프 안에서 만들면 같은
    # 시세에서 나온 근거들이 서로 다른 좌표를 갖게 된다 (실제로 그랬다 — Codex 2차 지적).
    quote_ref = f"MQ-가락-{observed_at(state['market_quotes']) or state['date']}"

    scenarios = []
    dropped = []
    # 🔴 **되돌려서 timing 을 잃은 라벨.** ⑦ 이 「축이 왜 안 쓰였나」를 가를 때 쓴다 —
    #   «되돌려서» 와 «다른 검사에서 탈락해서» 는 다른 사실이고, 뒤쪽이면 축 목록을
    #   좁히면 안 된다 (그 탈락을 가리게 된다).
    되돌린_라벨: list[str] = []
    for draft in drafts:
        # 🔴 **분할이 실제로 서는지 만들어 보고 정한다** (2026-09-16 · E3-9 앞단).
        #   ③ 이 분할 전제로 넓힌 수량은 «후보» 지 «보증» 이 아니다 — 회차 수 · 배분 비율 ·
        #   실제 도착일을 적용하고 누적을 통과해야 분할이 성립한다. 안 서면 **일괄 기준으로
        #   되돌린다** — 안 되돌리면 넓힌 수량이 1회차로 나가 ⑦ 이 통째로 컷하고,
        #   **원래 살아 있던 작은 일괄안까지 사라진다.**
        #
        # ★ **④ 가 판단자를 부를지 정할 때 같은 함수를 미리 돌린다** (2026-09-17 ·
        #   ``settle_split``). 여기서 버릴 배분을 ④ 가 고르게 두지 않기 위해서다.
        원래_축 = axes[draft["label"]]
        결과 = settle_split(
            draft,
            원래_축,
            split_choice,
            by_trend=bool(split_facts.get("by_trend")),
            constraints=constraints,
            as_of=state["date"],
            lead_days=lead_days,
            cap_by_date=cap_by_date,
            calendar=calendar,
        )
        draft, 쓸_비율, bulk_note = 결과.draft, 결과.ratios, 결과.note
        # 🔴 **배분 판단의 적용 여부는 ④ 의 분할을 받을 안에만 적는다.** 축을 못 받은 안이
        #   넓힌 수량 때문에 되돌아간 것은 배분 판단과 무관하다.
        배분_대상 = 원래_축 == TIMING_AXIS and bool(split_facts.get("entered"))
        if 결과.kind == ROLLED_BACK:
            # 되돌린 안은 timing 을 **잃는다** — 회차가 하나면 그것은 일괄안이고,
            # 라벨만 timing 으로 두면 §3.5.1-3 이 막는 "3안인데 사실 한 안"이 된다.
            # 🔴 **되돌린 라벨을 적어 둔다** — ⑦ 이 「축이 왜 안 쓰였나」를 가를 때
            #   «되돌려서» 와 «다른 검사에서 탈락해서» 를 구분해야 한다 (2026-09-16 검토).
            axes[draft["label"]] = "quantity"
            되돌린_라벨.append(draft["label"])
        total = draft["total_qty_kg"]
        if total <= 0:
            # 수량이 0이라 안이 될 수 없다 (스키마가 total_qty_kg > 0을 요구한다). 조용히
            # 사라지지 않게 사유를 남긴다 — 안이 왜 없는지가 소비자에게 보여야 한다.
            dropped.append(no_quantity_reason(draft))
            continue
        sourcing = materialize_sourcing(total, state["sourcing_plan"])
        # 분할은 **timing 축을 받은 안에만** 붙는다 (§4-④ E3-3 확정 1). 전 안에 걸면
        # 세 안의 split 구조가 같아져 timing이 라벨로만 남는다 — §3.5.1-3이 막으려는 상태다.
        axis = axes[draft["label"]]
        chosen = 쓸_비율 if axis == TIMING_AXIS else None
        coverage_days = draft["coverage_days"]
        # ★ 회차 금액을 여기서 얹는다 — ``sourcing`` 이 있어야 계산되므로
        #   ``materialize_split`` 안이 아니라 밖이다. 이후 ``split_plan`` 과
        #   ``payment_schedule`` 이 **같은 목록**을 본다.
        rounds = with_round_amounts(
            materialize_split(
                state["date"],
                total,
                chosen,
                coverage_days,
                lead_days=lead_days,
                cap_by_date=cap_by_date,
                calendar=calendar,
            ),
            sourcing,
        )
        rationale_input = {**draft, "daily_demand_kg": base["daily_demand_kg"]}
        unit_price = weighted_unit_price(sourcing, total)
        margin_warning, expected_margin_rate = compute_margin(unit_price, contract_price)
        # ⚠️ **둘을 따로 부른다 — 지금은 같은 값이다** (`compute_cut_unit_price` 참조).
        #   재무 STRESS 로 나가는 것은 ``max_price`` 뿐이고, 컷은 ``cut_unit_price`` 가 한다.
        max_price = compute_max_price(state["forecast"], draft["coverage_days"])
        cut_unit_price = compute_cut_unit_price(state["forecast"], draft["coverage_days"])
        scenarios.append(
            {
                "label": draft["label"],
                "strategy_type": axis,
                "coverage_days": draft["coverage_days"],
                "total_qty_kg": total,
                "total_amount_krw": sum(
                    line["qty_kg"] * line["grade_unit_price"] for line in sourcing
                ),
                # 🔴 **재무 STRESS 전용이다** — 컷은 ``cut_unit_price`` 가 한다.
                "max_price": max_price,
                "cut_unit_price": cut_unit_price,
                # 규칙 5 — 계약단가 초과는 컷이 아니라 표시다.
                "margin_warning": margin_warning,
                "split_plan": rounds,
                "sourcing_plan": sourcing,
                # 분할 안이고 N5를 받은 날만 실린다 — 아니면 **키 자체가 없다**.
                # ★ **``max_price`` 다 — ``cut_unit_price`` 가 아니다.** 재무·마스터가
                #   ``amount_max_krw == qty × max_price`` 를 검사한다.
                **payment_schedule_field(
                    rounds,
                    max_price,
                    pending_value(state, constraints, "purchase_payment_days"),
                ),
                "expected_margin_rate": expected_margin_rate,
                "rationale": [
                    *rationale(state, rationale_input, constraints, quote_ref),
                    *context_rationale(state["context_docs"]),
                    *sourcing_rationale(decision, quote_ref),
                    *split_rationale(
                        split_facts,
                        rounds,
                        state["forecast"],
                        state["date"],
                        total_qty_kg=total,
                        outcome=결과,
                    ),
                    *(
                        allocation_choice_rationale(
                            split_facts, 결과, rounds, total, state["forecast"], state["date"]
                        )
                        if 배분_대상
                        else []
                    ),
                ],
                "risks": [
                    *risks(draft, base["deferred_checks"], lots, state["date"]),
                    # 🔴 **되돌린 사실은 숨기지 않는다** (E3-9 앞단). ③ 이 분할 전제로 넓힌
                    #   수량이 실제로는 안 서서 일괄 기준으로 내려왔다는 것은, 읽는 사람이
                    #   *"왜 이만큼밖에 안 사나"* 를 묻는 자리다.
                    *([bulk_note] if bulk_note else []),
                    *forecast_risks(state["forecast"], draft["coverage_days"], constraints),
                    *adjustment_risks(
                        state.get("adjustments"),
                        constraints,
                        draft["label"],
                        # 물렸으면 ``risks`` 가 이미 축소 문장을 냈다 — 겹쳐 적지 않는다.
                        clipped=any(
                            clip["constraint"] == ADJUSTMENT_CAP_NAME
                            for clip in draft["clipped_by"]
                        ),
                    ),
                    *context_risks(
                        state["context_loop_count"],
                        state["context_docs"],
                        state["date"],
                        # 🔴 **빈 문서가 두 뜻이라 넘긴다** — "그날 없었다"와 "못 읽었다".
                        #   State 는 갈라 들고 있는데 ⑥이 안 읽어서 문면이 하나였다.
                        state.get("context_unavailable"),
                    ),
                    *sourcing_risks(sourcing, decision),
                    *(allocation_risks(split_facts, 결과) if 배분_대상 else []),
                    *split_risks(
                        split_facts,
                        axis,
                        total,
                        coverage_days,
                        chosen,
                        rounds,
                        as_of=state["date"],
                        lead_days=lead_days,
                        cap_by_date=cap_by_date,
                        calendar=calendar,
                        # 축이 걷힌 안 하나에만 붙는다 (`#308`) — 세 안에 다 붙이면
                        # 같은 사실이 세 번 나가고, 안 붙이면 열린 축을 아무도 안 쓴
                        # 이유가 사라진다.
                        timing_withdrawn=declared_axes[draft["label"]] == TIMING_AXIS
                        and axis != TIMING_AXIS,
                    ),
                    *payment_risks(
                        rounds,
                        pending_value(state, constraints, "purchase_payment_days"),
                        state.get("critical_payment_dates"),
                    ),
                ],
            }
        )

    return {
        "scenarios_final": scenarios,
        "rejected_reasons": [*state["rejected_reasons"], *dropped],
        "confidence": constraints["situation"]["confidence_by_situation"][state["situation"]],
        # 🔴 **⑦ 이 「축이 왜 안 쓰였나」를 가르는 데 쓴다** (2026-09-16 검토).
        #   이 목록이 비어 있으면 timing 을 안 쓴 이유가 되돌림이 아니라는 뜻이고,
        #   그때 축 목록을 좁히면 **다른 검사에서의 탈락을 가린다.**
        "split_rolled_back_labels": 되돌린_라벨,
    }
