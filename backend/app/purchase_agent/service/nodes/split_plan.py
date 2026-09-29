"""④ split_plan — 조건부 진입 + 분할 유형 선택 (상세설계 §4-④ · 백로그 E3-3).

**계산만 한다** (규칙 6). 진입 여부와 회차 수는 규칙이 정하고, LLM은 회차별 수량·날짜
배분 판단만 맡는다 (다음 단계 — §4-④ "LLM은 회차별 수량·날짜 배분 판단만").

§4-④ E3-3 확정(8/25)이 이 파일의 구조를 정했다:

* **적용 범위 = timing 축을 받은 안에만** (확정 1). 그 판정은 축을 배정하는 ⑥이 한다 —
  여기서는 "그날 분할이 가능한가"까지만 정한다.
* **궤적 판정은 ①의 ``judge_sustained_rise()`` 재사용** (확정 2). 두 노드가 각자 정의하면
  "축은 열렸는데 분할은 안 되는" 모순이 난다. (2026-09-17 정의 교체 때 이름이
  ``is_sustained_rise`` 에서 바뀌었다 — 판정 결과가 참/거짓 둘에서 넷으로 갈렸다.)
* **rule_only 단계는 균등 비율** (확정 3). 앞당길지 미룰지는 §4-④ 트레이드오프
  ("상승장 분할 = 평균단가 손해 vs 로트 나이 분산 = 폐기리스크 감소")의 판단이라 LLM 몫이고,
  규칙이 한쪽으로 기울이면 그 판단을 미리 대신해버린다.
* **④는 비율만, 날짜는 ⑥이 안별 D로 만든다** (확정 4). ⑤가 비율만 내고 ⑥이 총량을 곱하는
  것과 같은 구조다 — 여기서 절대 날짜를 박으면 D가 다른 안에 같은 날짜가 박힌다.

🟢 **자리 (2026-09-29 · 재구성 BL-016).** 전에는 `nodes/split_plan.py` 였다. 노드 함수와 ④ 배분
  판단자를 부르는 도우미(`_choose_allocation`)만 남기고, 판정 · 계산은 `domain/split_plan.py` 로
  옮겼다.
"""

from typing import Any

from app.purchase_agent.config import load_constraints
from app.purchase_agent.domain.classify_situation import split_entry_cap
from app.purchase_agent.domain.split_plan import (
    CANDIDATE_SUMMARY,
    evaluate_split_entry,
    screen_allocation_candidates,
)
from app.purchase_agent.features import SPLIT_ALLOCATION, enabled
from app.purchase_agent.llm.split_allocation import SplitAllocationSelector
from app.purchase_agent.llm.split_allocation import build_context as build_split_context
from app.purchase_agent.llm.split_schemas import (
    SplitAllocationChoice,
    SplitAllocationResult,
    SplitCandidate,
)
from app.purchase_agent.schemas.state import PurchaseAgentState


def _choose_allocation(
    state: PurchaseAgentState,
    constraints: dict,
    decision: dict,
    후보: dict[str, list[float]],
    selector: SplitAllocationSelector | None,
) -> tuple[str, SplitAllocationResult | None]:
    """어느 배분으로 갈지 고른다. **기본은 늘 균등이다.**

    🔴 **꺼져 있거나 후보가 하나면 판단자를 안 부른다.** 고를 것이 없는데 부르면 비용만
      들고 상태만 흐려진다 — ⑤ 의 ``needs_llm`` 과 같은 자리다.

    🔴 **고르는 것은 id 하나뿐이다.** 비율은 규칙이 이미 만들었고 안전 검사까지 끝냈다.
      돌아온 id 가 후보 밖이면 검증이 막고 기본안으로 떨어진다.

    ⚠️ 실패·비활성이면 ``BASE_EQUAL`` 이라 산출물이 **붙이기 전과 같다** — 회귀가 아니라
      무변화다.
    """
    if selector is None or not enabled(SPLIT_ALLOCATION):
        return "BASE_EQUAL", None
    if not decision["entered"]:
        # 🔴 **진입하지 않은 날은 선택이라는 단계 자체가 없다** (2026-09-17). 전에는 여기서도
        #   판단자를 불러 «건너뛰었다» 가 매일 흔적에 남았다 — 어댑터
        #   ``_split_allocation_call`` 이 약속한 «진입 안 한 날은 줄을 안 남긴다» 와 달랐다.
        return "BASE_EQUAL", None
    cap = split_entry_cap(state, constraints)
    context = build_split_context(
        state["item"],
        rounds=decision["rounds"],
        rising=bool(decision["by_trend"]),
        cap_tight=None if cap.cap_kg is None else bool(decision["by_volume"]),
        signals=[
            이름
            for 이름, 켜짐 in (
                ("SPLIT_ENTERED_BY_VOLUME", decision["by_volume"]),
                ("SPLIT_ENTERED_BY_TREND", decision["by_trend"]),
            )
            if 켜짐
        ],
        facts=["규칙이 만든 배분 후보 중 하나를 고른다."],
        candidates=[
            SplitCandidate(candidate_id=이름, summary=CANDIDATE_SUMMARY[이름])
            for 이름 in 후보
        ],
    )
    result = selector(context, "BASE_EQUAL")
    고른 = result.interpretation.chosen_candidate_id
    if 고른 not in 후보:
        return "BASE_EQUAL", _되돌린다(result)
    return 고른, result


def _되돌린다(result: SplitAllocationResult) -> SplitAllocationResult:
    """후보 밖 id 를 들고 온 판단을 **실패로 표시해** 되돌린다 — ⑤ ``_select_mix`` 와 같은 자리.

    🔴 **검증기만으로는 부족하다.** ``validate_choice`` 는 서비스 안의 문이고, ``selector``
      는 주입 가능한 콜러블이라 그 문을 **우회할 수 있다.** 우회하면 고른 후보만 균등으로
      되돌아가고 **판단자가 쓴 사유는 그대로 남아**, 근거에 「회차 배분 회차를 고르게
      나눈다(BASE_EQUAL) 선택 — <남의 사유>」가 나갔다. 라벨과 문장이 어긋나는 상태다.

    🔴 **``None`` 으로 지우지 않는다.** 지우면 되돌린 사실까지 사라져 「판단자가 이상한
      값을 줘서 되돌렸다」가 소비자에게 안 보인다. 실패로 표시해 ⑥ 이 ``risks`` 에 고지를
      싣게 한다 — 그래야 실행 흔적(``llm_calls``)의 상태와도 서로를 부정하지 않는다.

    ⚠️ 사유도 같이 기본안으로 되돌린다. 상태만 ``FALLBACK`` 으로 바꾸고 문장을 두면 그
      문장이 계속 어딘가로 실려 나갈 길이 남는다 — 지금 근거가 실어 나르던 그 길이다.
    """
    return result.model_copy(
        update={
            "interpretation": SplitAllocationChoice(
                chosen_candidate_id="BASE_EQUAL", reason="규칙 기본안"
            ),
            "llm_status": "FALLBACK",
            "llm_fallback_used": True,
        }
    )


def split_plan(
    state: PurchaseAgentState, *, selector: SplitAllocationSelector | None = None
) -> dict[str, Any]:
    """분할 유형을 고르고 회차 비율을 낸다. 진입하지 않으면 ``None``(일괄)이다.

    E3-2에서 LLM이 붙는 자리는 여기다: ``evaluate_split_entry``가 낸 사실들(트리거 종류·
    총량·회차 수)과 예측 궤적을 프롬프트로 주고 **회차별 수량·날짜 배분**을 판단하게 한다 —
    "상승장이라 앞당기면 단가는 유리하지만 로트가 한꺼번에 늙는다. 어느 쪽인가?"
    유형은 그때도 고정 목록에서 고르고(§4-④), 숫자는 계산이 소유한다 (규칙 6).

    수량은 여기서 정하지 않는다 — 안별 총량이 달라 회차 수량은 ⑥이 materialize한다.
    이 노드가 소유하는 건 **유형**이고, 그 층위는 IO명세 feedback의
    ``keep: ["sourcing_ratio", "split_type"]``과 같다.

    **일괄(진입 안 함)도 ``None``이 아니라 1회차 비율 목록으로 낸다.** 진입하지 않은 이유가
    ``decision``에 실려 ⑥까지 가야 하기 때문이다 — ①은 클립 **전** 추정 총량으로 timing 축을
    열고 ④는 클립 **후** 실제 총량으로 판정하므로, "timing 라벨인데 회차가 하나"인 안이
    정상적으로 생긴다. ``None``으로 내보내면 그 안이 왜 그런지 설명할 근거가 사라진다.
    비율 1.0짜리 한 줄은 ⑥에서 단일 회차로 materialize돼 결과가 일괄과 같다.
    """
    constraints = load_constraints()
    decision = evaluate_split_entry(state, constraints)
    거름 = screen_allocation_candidates(
        state, constraints, decision["rounds"], by_trend=bool(decision["by_trend"])
    )
    후보 = 거름.kept
    고른, 판단 = _choose_allocation(state, constraints, decision, 후보, selector)
    decision["allocation_candidates"] = sorted(후보)
    # 🔴 **왜 판단자를 안 불렀나를 가른다** — 승인 전인가, 적용되지 않을 것이 확정됐나.
    #   어댑터가 흔적의 ``skip_reason`` 을 이 둘로 쓴다.
    decision["allocation_approved"] = 거름.approved
    decision["allocation_excluded"] = dict(sorted(거름.excluded.items()))
    decision["allocation_chosen"] = 고른
    # 🔴 판단 흔적을 **결과와 함께** 들고 다닌다 — ⑥ 이 그 사실을 risks 에 적고
    #   어댑터가 실행 흔적에 역할별로 남긴다. 상태와 결과가 갈리면 서로를 부정한다.
    decision["allocation_judgment"] = 판단
    lines = [{"ratio": ratio} for ratio in 후보[고른]]
    # 판단 근거를 첫 줄에 싣는다 — State 필드를 늘리지 않기 위해서다 (§3 계약).
    # ⑥의 materialize가 계약 필드만 투영하므로 출력에는 새지 않는다 (⑤와 같은 방식).
    lines[0] = {**lines[0], "decision": decision}
    return {"split_plan": lines}
