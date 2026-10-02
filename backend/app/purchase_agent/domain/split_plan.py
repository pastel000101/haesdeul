"""④ split_plan — 분할 진입 · 회차 수 · 배분 후보 걸러내기 — 판정 · 계산 (입력 → 출력만).

노드 함수는 `service/nodes/split_plan.py` 에 있고, 여기 함수들을 순서대로 부른다.
다른 노드 · 어댑터 · 검사가 이 판정을 다시 부르므로 노드 밖에 둔다.
"""

from math import ceil
from typing import Any, NamedTuple

from app.purchase_agent.domain.allocation import (
    APPROVED,
    allocation_candidates,
    arrival_dates,
    assign_axes,
    occupancy_fits,
    split_quantities,
)
from app.purchase_agent.domain.classify_situation import judge_sustained_rise, split_entry_cap
from app.purchase_agent.domain.guards import pending_value
from app.purchase_agent.domain.split_outcome import AS_CHOSEN, settle_split
from app.purchase_agent.schemas.proposal import TIMING_AXIS
from app.purchase_agent.schemas.state import PurchaseAgentState


def largest_total_kg(base_plan: dict) -> int:
    """가장 큰 안의 클립 전 수량. 수량 트리거의 비교 대상이다.

    ①도 같은 트리거를 보지만 그때는 수량이 없어 추정 총량(일평균 × 최대 D)을 쓴다.
    ④는 ③ 뒤라 실제 안별 수요를 본다.

    ``total_qty_kg`` 가 아니라 ``raw_qty_kg`` 다. 클립 뒤 총량을 보면, 창고 상한이 곧
      ``cap_by_date`` 이므로 클립이 걸린 날은 총량 == 그 상한이 되고 ``총량 > 상한`` 이
      구조적으로 거짓이 된다. 측정(REH-0914)에서 그 등호가 163일 중 161일에 성립했다 —
      분할이 필요한 날일수록 진입 판정이 막히는 모양이다.

    묻는 질문이 「한 번에 다 들어가는가」다. 그 답은 깎기 전 수요가 알고 있다.
      깎은 뒤 수를 물으면 «깎았으니 들어간다» 라는 동어반복이 된다.

    ``raw_qty_kg`` 는 차감 뒤 · 클립 전이다 (③ ``draft_one``). 보유 차감은 필요가
      줄어든 것이지 천장이 아니라서 빼는 것이 맞고, 창고·현금·신선도·조정안 클립만
      되돌린 값이다.

    ``volume_gate_holds`` 의 불변식은 그대로 선다 — ``raw_qty ≤ demand_qty =
      round(일평균 × D) ≤ ceil(추정)`` 이라 ① 이 여전히 ④ 보다 먼저 열린다.
    """
    return max((draft["raw_qty_kg"] for draft in base_plan["drafts"]), default=0)


def choose_rounds(total_kg: int, cap_kg: float | None, constraints: dict) -> int:
    """회차 수를 고정 목록에서 고른다 (§4-④ "생성 말고 선택").

    ``clamp(ceil(총량 / 도착일 여유), 목록 경계)``. 여유 하나당 한 회차이고, 진입했으면
    최소 2회차다 — 그 "2"는 상수가 아니라 목록에서 1 다음으로 작은 유형이다.

    분모는 도착일 여유다 (`#308`). "고정 임계짜리 덩어리 몇 개인가" 가 아니라
      "그날 들어갈 만큼씩 나누면 몇 번인가" 다. 근거는 ① ``split_entry_cap`` docstring.

    회차 수는 매입이 정한다 — 물류가 붙인 조건 그대로다. 여유는 물류 정본을 읽고,
      그것으로 몇 번에 나눌지를 정하는 것은 이쪽 판단이다.

    여유 ``0`` 은 나눗셈이 아니라 «아무리 나눠도 그날엔 안 들어간다» 다. ``ceil``
      로는 ∞ 라 목록 최대로 클램프한다. 0으로 나누기를 피하려는 방어가 아니라 뜻을
      옮긴 것이다 — 뒤에서 ⑦ ``check_arrival_capacity`` 가 그 안을 어차피 컷한다.

    ``cap_kg`` 가 ``None`` 이면 여기까지 오지 않는다 — ``evaluate_split_entry`` 가
    수량 트리거를 세우지 않으므로, 진입했다면 궤적으로 진입한 것이라 하한만 걸린다.

    주의: 측정한 데이터에서는 나눠도 도착일 컷을 못 피한다.

      ⑦ ``check_arrival_capacity`` 는 도착일까지의 누적을 그날 여유와 견준다
      (앞 회차가 아직 창고에 있으므로 옳다). 그런데 측정에서 ``cap_by_date`` 는
      창 전체가 한 값이다 — 원장 2,743 봉투 전부 그렇고, 끝−처음이 0 이다
      (2026-09-12). 누적은 늘고 여유는 안 늘면, 나누는 것으로는 그 컷을 못 넘는다.

      그래서 이 조항이 컷된 안을 되살리는 효과는 0이다. 값은 다른 데 있다 — 기준의 뜻,
        근거 문장, 그리고 로트 나이 분산이다. 창이 날짜별로 실제로 갈리는 날
        (재고가 실제로 나가기 시작하면) 이 문단을 다시 재야 한다.
    """
    types = sorted(constraints["split"]["types"])
    splittable = [size for size in types if size > 1]
    if not splittable:
        return 1
    if cap_kg is None:
        chunks = 1  # 궤적 진입 — 여유를 못 봤으므로 수량으로는 회차를 못 정한다
    elif cap_kg <= 0:
        chunks = max(splittable)
    else:
        chunks = ceil(total_kg / cap_kg)
    return min(max(chunks, min(splittable)), max(splittable))


def evaluate_split_entry(state: PurchaseAgentState, constraints: dict) -> dict[str, Any]:
    """진입 판정과 회차 수. 근거 전체를 dict 하나로 돌려준다.

    ``timing ∈ allowed_axes AND (최대안 총량 > 도착일 여유 OR 지속 상승 궤적)``
    (§4-④ v1.1 정정 — 구 "D ≥ 임계"는 낡은 표현이고 임계는 수량이다).

    수량 가지의 기준은 도착일 여유다 (`#308`). 고정 임계 ``20,000kg`` 로는 이 가지가
      원장 전수에서도 거의 안 섰다 — 안이 있는 672셀 중 1셀뿐이다(2026-09-12 측정).
      품목별 최대가 배추 8,727 · 무 9,429 · 양파 10,286kg 이라 구조적으로 미만이었다.
      근거는 ① ``split_entry_cap`` docstring.

    ①과 여기가 다른 수를 본다 — 그게 정상이다. ①은 추정 총량(일평균 × 최대 D)으로
      축을 열고, ④는 ③ 뒤 안별 실제 수요(``largest_total_kg``)로 진입을 본다.
      추정으로 열린 축이 실제 수량에서 닫히는 날이 생기고 (측정 20셀 · 2026-09-12),
      그 안은 timing 라벨만 남고 회차가 하나가 된다. 그 상태를 ⑥·⑦이
      ``effective_allowed_axes`` 로 걷는다 — 안 걷으면 «분할 안 한 분할안» 이 선다.
    """
    total_kg = largest_total_kg(state["base_plan"])
    cap = split_entry_cap(state, constraints)
    trend = judge_sustained_rise(state["forecast"], constraints)

    facts: dict[str, Any] = {
        "timing_allowed": TIMING_AXIS in state["allowed_axes"],
        "largest_total_kg": total_kg,
        # 칸 이름이 ``threshold_kg`` 가 아니라 ``cap_kg`` 다. 같은 칸에 다른 뜻을 넣으면
        # 근거 문장이 "임계"라고 말하면서 창고 여유를 인용한다.
        "cap_kg": cap.cap_kg,
        "arrival_date": cap.arrival_date,
        "cap_unknown_reason": cap.unknown_reason,
        # ``>`` 다 — ``>=`` 가 아니다 (`#308`). ⑦ ``check_arrival_capacity`` 의 컷이
        # ``occupied > cap`` 이라, 총량이 여유와 같은 날은 1회차로 정확히 들어간다. 거기서
        # 진입하면 나눌 이유가 없는데 나뉘고, ``timing`` 라벨이 timing 근거 없이 붙는다.
        #
        # ``cap_kg`` 는 소수일 수 있는데 ``total_kg`` 는 정수라, 이 비교는 ⑦ 이
        # ``int(cap)`` 으로 내림해 재는 것과 모든 경우에 같은 답을 낸다.
        "by_volume": cap.cap_kg is not None and total_kg > cap.cap_kg,
        "by_trend": trend.holds,
        # 판정 결과를 넷으로 싣는다. 참/거짓만 실으면 «판정 보류» 와
        # «실제 하락» 이 같은 거짓으로 읽힌다 — ⑥ 고지와 흔적이 이 칸으로 가른다.
        "trend_verdict": trend.verdict,
        "trend_withheld_reason": trend.withheld_reason,
        # 비교 지점(lead_time 게이트 행 포함)과 최소 개수 산정 지점(확인된 모델 예측)을 가른다
        "trend_compared_points": len(trend.points),
        "trend_model_points": trend.model_points,
        "trend_flags_reported": trend.flags_reported,
        "trend_first_decline": list(trend.first_decline) if trend.first_decline else None,
        "rounds": 1,
    }
    facts["entered"] = facts["timing_allowed"] and (facts["by_volume"] or facts["by_trend"])
    if facts["entered"]:
        # 회차 수는 «실제로 살 양» 으로 정한다. 진입 여부를 묻는 수와 몇 번에 나눌지를
        # 정하는 수가 다르다 —
        #
        #     진입   한 번에 다 들어가는가        ← 깎기 전 수요 (largest_total_kg)
        #     회차   몇 번에 나눠야 들어가는가    ← 깎은 뒤 총량 (아래 clipped)
        #
        # 깎기 전 수요로 회차를 정하면 현금·신선도·조정안이 이미 줄여 놓은 양을 필요보다
        # 여러 번에 나눈다 (측정 예: raw 12,429 · 실제 2,000 인데 3회차).
        clipped = max(
            (draft["total_qty_kg"] for draft in state["base_plan"]["drafts"]), default=0
        )
        facts["largest_clipped_kg"] = clipped
        facts["rounds"] = choose_rounds(clipped, cap.cap_kg, constraints)
    return facts


def split_decision(chosen: list[dict] | None) -> dict:
    """④가 첫 줄에 실어 보낸 분할 판단 근거. ⑤의 ``sourcing_decision``과 같은 방식이다.

    ⑥ · ⑦ 이 같은 값을 읽으므로 판단을 만든 쪽(④)이 읽는 법도 들고 있다 (`#308`).
    """
    return chosen[0].get("decision", {}) if chosen else {}


def effective_allowed_axes(allowed_axes: list[str], chosen: list[dict] | None) -> list[str]:
    """실효 축 — ④가 실제로 안 나눴으면 ``timing`` 을 뺀다 (`#308`).

    왜 필요한가. ①은 추정 총량으로 축을 열고 ④는 ③ 뒤 실제 수요로 진입을 본다.
      축은 열렸는데 진입은 안 한 날이 생기고, 그날 ⑥이 그대로 ``timing`` 을
      배정하면 회차가 하나인 «분할안» 이 선다 — §3.5.1-3 이 막으려는 "3안인데 사실
      한 안"이 라벨로만 위장한 꼴이다.

    ⑥만 고치면 ⑦이 그 안들을 통째로 죽인다. ⑥이 timing 을 안 주면 전 안이
      ``quantity`` 가 되는데, ⑦ ``check_axis_diversity`` 는 ``allowed_axes`` 가 둘 이상인
      날 전 안 동일 축을 반려한다. 측정으로 20셀이 그렇게 사라진다 (2026-09-12 ·
      안이 있는 672셀 기준 · 원장 재생). 그래서 ⑥과 ⑦이 같은 목록을 본다 — 이 함수가
      그 목록이다.

    ``quantity`` 는 안 뺀다. 수량 축은 ①이 늘 여는 축이라 뺄 조건이 없다.
    """
    if split_decision(chosen).get("entered"):
        return allowed_axes
    return [axis for axis in allowed_axes if axis != TIMING_AXIS]


#: 후보를 목록에서 뺀 사유 코드 — 실행 흔적과 검사가 읽는다. 사람 문장이 아니다.
#: ⑥ 이 적용하지 않을 갈래는 ``domain/split_outcome`` 의 이름(``ROUNDS_CHANGED`` ·
#: ``ROLLED_BACK``)을 그대로 쓴다 — 같은 판정이 이름 둘을 갖지 않게.
EXCLUDED_NO_SPLIT_PLAN = "NO_SPLIT_PLAN"
EXCLUDED_OVER_CAPACITY = "OVER_CAPACITY"


class CandidateScreen(NamedTuple):
    """선택 전에 거른 결과. 남긴 것 · 뺀 것 · 승인 여부를 같이 들고 다닌다."""

    kept: dict[str, list[float]]
    #: 뺀 후보 id → 사유 코드
    excluded: dict[str, str]
    #: 선언 ``status`` 가 정확히 ``APPROVED`` 였나
    approved: bool


def screen_allocation_candidates(
    state: PurchaseAgentState,
    constraints: dict,
    rounds: int,
    *,
    by_trend: bool | None = None,
) -> CandidateScreen:
    """규칙이 만든 후보 중 결과에 그대로 적용될 것만 남긴다 (E3-9).

    선택 전에 거른다. LLM 이 고른 뒤에 ⑥ 이 버리거나 ⑦ 이 컷하면, 사람은 "판단자가
      이상한 걸 골랐다" 로 읽는다. 실제로는 규칙이 못 쓸 후보를 목록에 올린 것이다.

    ⑥ 과 같은 함수(``settle_split``)로 잰다. ④ 가 «날짜별 여유에 드는가» 만 보고 ⑥ 이
      «나눌 실익이 있나 · 그 회차 수가 서나» 를 더 보면, 창이 한 값인 날 ④ 는 세 후보를
      올려 판단자를 부르고(SUCCESS) ⑥ 은 그 선택을 버리고 한 번에 산다 (실호출 실험에서
      그랬다). 그래서 한 후보는 둘 다 통과해야 남는다 —

        ㉠ ⑥ 이 그 비율을 그대로 적용한다 (``AS_CHOSEN`` — 회차를 바꾸거나 접지 않는다)
        ㉡ 그 비율의 수량이 옮기지 않고 날짜별 누적 여유에 든다 (``occupancy_fits``)

      ㉡ 을 따로 두는 이유 — ⑥ 은 여유를 넘는 회차를 옮겨서 세운다. 그러면 앞으로 싣는
      배분이 옮겨진 끝에 가운데가 무거운 배분으로 나가고, «고른 배분 = 나간 배분» 이 깨진다.

    분할을 받을 안만 본다. ⑥ 은 timing 축을 받은 안에만 비율을 편다 (§4-④ E3-3 확정 1).
      나머지 안은 어느 후보를 골라도 한 번에 사므로, 거기서 재면 쓰이지도 않을 계산 때문에
      후보가 사라진다. 축 배정은 ⑥ 과 같은 ``assign_axes`` 다 — 진입한 날 실효 축은 연 축과 같다.

    적용될 비균등 후보가 하나도 없으면 판단자를 안 부른다 — 목록에 ``BASE_EQUAL`` 하나가
      남고 ``needs_call`` 이 거짓이 된다. 다만 하나라도 적용되면 부른다. ``BASE_EQUAL``
      자신이 ⑥ 에서 바뀔 날이어도 그렇다 — 그것은 «되돌아갈 자리» 라 거르지 않는다.

    못 보면 안 올린다 (규칙 3). 도착일이나 날짜별 여유를 모르면 ㉡ 가 거짓이다.
    """
    선언 = constraints["split"]["allocation_weights"]
    approved = 선언.get("status") == APPROVED
    후보 = allocation_candidates(선언, rounds)
    남긴다 = {"BASE_EQUAL": 후보["BASE_EQUAL"]}
    if len(후보) == 1:
        return CandidateScreen(남긴다, {}, approved)
    if by_trend is None:
        by_trend = judge_sustained_rise(state["forecast"], constraints).holds
    drafts = state["base_plan"]["drafts"]
    배정 = assign_axes(
        [draft["label"] for draft in drafts],
        list(state.get("allowed_axes") or []),
        constraints["allocation"]["aggressive_axis"],
    )
    나눌_안 = [draft for draft in drafts if 배정.get(draft["label"]) == TIMING_AXIS]
    걸렀다: dict[str, str] = {}
    for 이름, 비율 in 후보.items():
        if 이름 == "BASE_EQUAL":
            continue
        사유 = _적용되지_않는_사유(state, constraints, 나눌_안, 비율, by_trend=by_trend)
        if 사유 is None:
            남긴다[이름] = 비율
        else:
            걸렀다[이름] = 사유
    return CandidateScreen(남긴다, 걸렀다, approved)


def safe_allocation_candidates(
    state: PurchaseAgentState,
    constraints: dict,
    rounds: int,
    *,
    by_trend: bool | None = None,
) -> dict[str, list[float]]:
    """``screen_allocation_candidates`` 가 남긴 것만."""
    return screen_allocation_candidates(state, constraints, rounds, by_trend=by_trend).kept


def _적용되지_않는_사유(
    state: PurchaseAgentState,
    constraints: dict,
    나눌_안: list[dict],
    비율: list[float],
    *,
    by_trend: bool,
) -> str | None:
    """이 비율이 결과에 그대로 적용되지 않는 사유. 적용되면 ``None``.

    ③·⑥ 이 쓰는 칸을 그대로 읽는다 — 총량은 ``total_qty_kg``, 커버는 안이 들고 있는
      ``coverage_days``, 달력은 State 최상위 ``execution_calendar`` 다. 다른 데서 읽으면
      사전검사와 실제 회차일이 다른 달력을 보게 된다 (`#662`).
    """
    if not 나눌_안:
        return EXCLUDED_NO_SPLIT_PLAN
    lead_days = pending_value(state, constraints, "inbound_lead_days")
    cap_by_date = (state.get("inventory") or {}).get("cap_by_date")
    calendar = state.get("execution_calendar")
    회차 = [{"ratio": ratio} for ratio in 비율]
    for draft in 나눌_안:
        결과 = settle_split(
            draft,
            TIMING_AXIS,
            회차,
            by_trend=by_trend,
            constraints=constraints,
            as_of=state["date"],
            lead_days=lead_days,
            cap_by_date=cap_by_date,
            calendar=calendar,
        )
        if 결과.kind != AS_CHOSEN:
            return 결과.kind
        if not occupancy_fits(
            split_quantities(draft["total_qty_kg"], 회차),
            arrival_dates(state["date"], draft["coverage_days"], len(회차), lead_days, calendar),
            cap_by_date,
        ):
            return EXCLUDED_OVER_CAPACITY
    return None


#: 후보 id → 사람이 읽는 설명. 판단자에게도 이 말로 준다 — id 만 주면 무엇을
#: 고르는지 모르고, 숫자를 주면 그 숫자를 사유에 베껴 쓴다 (규칙 6).
CANDIDATE_SUMMARY = {
    "BASE_EQUAL": "회차를 고르게 나눈다",
    "FRONT_LOADED": "앞 회차에 더 싣는다",
    "BACK_LOADED": "뒤 회차에 더 싣는다",
}
