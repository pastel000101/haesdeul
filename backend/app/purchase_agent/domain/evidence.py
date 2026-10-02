"""봉투 회신의 근거(Evidence) — 판정 입력을 다시 재어 숫자에 출처를 붙인다 (M-1 §7).

판정을 새로 하지 않는다 — ①·③ 이 쓴 같은 함수(`domain/classify_situation.py`)로 같은
값을 다시 재고, 그 값에 출처 · 단위 · 등급을 단다. 어댑터(`adapter.py`)가 회신을 만들 때
`build_evidences` 를 부른다.
"""

from collections.abc import Mapping
from math import ceil, floor
from typing import Any

from app.contracts.core import Evidence
from app.purchase_agent.config import ci_width_threshold
from app.purchase_agent.domain.classify_situation import (
    SplitEntryCap,
    compute_ci_width,
    compute_rise_rate_2w,
    coverage_by_label,
    estimate_daily_demand,
    judge_sustained_rise,
    split_entry_cap,
    sustained_rise_sentence,
    volume_gate_holds,
)


def _relation(value: float, threshold: float, comparison: str) -> str:
    """근거 문장에 쓸 부등호. 판정에 쓴 연산과 같은 방향을 돌려준다.

    ①이 ``ci_width_comparison``(설정값)으로 임계를 비교하므로 여기서도 그 문자열을 받아
    쓴다. 경계값에서 "0.080 > 0.08"처럼 거짓인 문장이 나오지 않게, 성립하지 않을 때는
    반대 방향을 적는다.
    """
    holds = value >= threshold if comparison == ">=" else value > threshold
    if holds:
        return "≥" if comparison == ">=" else ">"
    return "<" if comparison == ">=" else "≤"


def _volume_gate_sentence(estimated_total_kg: float, cap: SplitEntryCap) -> str:
    """총량 게이트(``by_volume``)의 근거 한 문장. 세 갈래다 (`#308`).

    갈래가 셋인 이유는 ⑦ ``ARRIVAL_SKIP_REASONS`` 와 같다 — "안 걸렸다" 와
    "못 봤다" 는 둘 다 «축이 안 열렸다» 이지만 왜 가 다르고, 하나로 적으면
    물류가 값을 안 보낸 날과 여유가 넉넉한 날이 화면에서 같은 문장이 된다.

    판정과 같은 함수(``split_entry_cap``)가 낸 값만 인용한다. 여기서 다시 세면
    근거가 실제 판정과 다른 수치를 주장하게 된다.
    """
    # 게이트가 «실제로 비교하는» 수를 적는다. ① 은 ③ 이 만들 수 있는
    # 최대치(``round`` 가 올림으로 떨어질 수 있어 ``ceil``)를 여유와 견주는데, 문장이
    # ``round`` 를 적으면 1kg 미만 경계에서 「8,607kg > 여유 8,608kg → 충족」 처럼
    # 눈으로 거짓인 줄이 나간다 — ``_relation`` docstring 이 막는 그 병이다.
    total = f"추정 총량 {ceil(estimated_total_kg):,}kg"
    if cap.cap_kg is None:
        where = f"{cap.arrival_date} 도착" if cap.arrival_date else "도착일"
        return f"{total} — {where} 창고 여유를 못 봐 총량 진입 조건을 판정하지 않았다"
    # 판정과 같은 술어(``volume_gate_holds``)를 부른다. 여기서 부등호를 다시 적으면
    # ① 의 판정이 바뀔 때 근거 문장만 옛 방향에 남는다 — 이 함수의 docstring 이 경고한
    # 그 병이다.
    holds = volume_gate_holds(estimated_total_kg, cap)
    # 여유는 내림해 적는다. 물류가 보내는 여유는 소수다 — 원장 실측(2026-09-12)에서
    # ``cap_by_date`` 값 79,291개 중 16,861개가 소수이고 대표값이 7,636.72 다.
    # ``:,.0f`` 는 반올림이라 7,637 로 적히고, 게이트가 성립한 날 화면이
    # "7,637kg > 여유 7,637kg → 충족" 이라는 눈으로 거짓인 줄을 내보낸다.
    #
    # 내림이 임의 선택이 아니다 — ⑦ ``check_arrival_capacity`` 가 ``int(cap)`` 으로
    # 같은 값을 읽고, ``domain/draft_plan.py`` 의 ``warehouse_cap_kg`` 도 "이 값은 상한이라
    # 올리면 못 넣는 양을 계획하게 된다" 며 내린다. 쓰는 쪽과 적는 쪽이 같은 수를 본다.
    return (
        f"{total} {'>' if holds else '≤'} {cap.arrival_date} 도착 여유 "
        f"{floor(cap.cap_kg):,}kg → 총량 진입 조건 {'충족' if holds else '미달'}"
    )


def build_evidences(
    state: Mapping[str, Any], payload: Mapping[str, Any], constraints: Mapping[str, Any]
) -> tuple[Evidence, ...]:
    """payload의 숫자·판정·비어 있지 않은 배열에 근거를 붙인다 (정의서 §1.2-5).

    봉투는 최상위 값 중 ``_needs_evidence``인 것 전부에 ``claim``이 같은 Evidence를
    요구하고, 반대로 payload에 없는 claim은 ``E-EVIDENCE-ORPHAN``으로 잡는다. 즉
    양방향이라 넘쳐도 모자라도 걸린다.

    그 규칙을 여기서 재구현하지 않는다. 어느 값이 근거를 요구하는지는 봉투가
    정하고, 우리는 아는 키에 대해 만든다. 규칙이 바뀌면 재구현이 조용히 어긋나므로,
    대신 실제 ``validate_reply``를 돌려 findings가 0인지 보는 테스트로 잠근다.

    ``allowed_axes``는 게이트마다 한 건이다 (신뢰도·총량·가격 경로·편중). 축 목록 하나에
    근거가 여럿인 이유는 축을 여닫는 조건이 여럿이기 때문이고, 하나로 합치면 나머지
    게이트의 수치가 사라진다.

    ``constraints`` 는 부서 선언(``constraints.yaml``)이다 — 부르는 쪽(어댑터)이 읽어 넘기고,
    여기서는 파일을 읽지 않는다.
    """
    # ``item`` 을 먼저 읽는다 — 임계가 품목별이라 품목 없이는 못 뽑는다. 근거 문장이
    # 판정과 다른 임계를 인용하지 않도록 ①과 같은 함수(``ci_width_threshold``)로 뽑는다.
    item = payload["meta"]["item"]
    threshold = ci_width_threshold(item, constraints)
    judgment_day = constraints["situation"]["ci_judgment_day"]
    as_of = payload["meta"]["as_of"]
    # ① 노드가 ``situation``·``allowed_axes``만 돌려주고 판정 수치는 남기지 않는다.
    # 여기서 같은 함수로 다시 구한다 — 값을 State에 얹으면 노드 계약이 바뀌고 949건이
    # 그 변화를 받는다. 같은 입력·같은 함수라 두 값이 갈릴 수 없다.
    ci_width = compute_ci_width(state["forecast"], judgment_day)
    axes = list(payload.get("allowed_axes") or [])
    # 편중 게이트가 보는 값 — 호출 품목이 아니라 전 품목의 최대비다.
    # mix 축은 품목을 조합하는 전략이라 "어느 품목이든 편중됐나"를 묻는다
    # (``classify_situation.compute_allowed_axes``와 같은 계산).
    ratios = (state.get("item_mix_ratio") or {}).values()
    top_mix_ratio = max(ratios) if ratios else 0.0
    mix_threshold = constraints["concentration"]["item_threshold"]
    # 부등호를 하드코딩하지 않는다 (규칙 7). ①이 임계와 비교 방향을 둘 다 파일에서
    # 읽으므로(``ci_width_comparison``), 근거 문장이 방향을 따로 적으면 설정을 ``>``로
    # 바꾼 날 문장만 옛 방향으로 남는다.
    comparison = constraints["situation"]["ci_width_comparison"]
    # 총량 게이트가 보는 값 — ①과 같은 계산(``coverage_by_label(situation, …)``)이다.
    # uncertain 인 날은 공격 라벨을 빼므로 최대 D 가 12 가 아니라 5다. 여기서 따로
    # ``max(by_label)``(늘 12)로 세면 근거가 판정보다 2.4배 큰 수를 인용한다 (`#308`)::
    #
    #       판정(①)   717.3 × 5  = 3,587kg
    #       근거(여기) 717.3 × 12 = 8,608kg
    #
    # 규칙 8 이 못 잡는 방향이다 — 선언을 바꾸면 둘 다 따라 움직이므로 변이가 안 문다.
    # `#342` · `#379` 와 같은 종류의 어긋남이다.
    estimated_total_kg = estimate_daily_demand(state["confirmed_orders"], constraints) * max(
        coverage_by_label(payload.get("situation"), constraints).values()
    )
    # 기준값도 ①과 같은 함수로 뽑는다 — 고정 임계가 아니라 도착일 창고 여유다 (`#308`).
    arrival_cap = split_entry_cap(state, constraints)
    # 가격 경로가 보는 두 값 — ①·④ 와 같은 함수로 다시 구한다 (위 ``ci_width`` 와 같은 이유).
    rise_rate = compute_rise_rate_2w(state["forecast"], judgment_day)
    rise_threshold = constraints["triggers"]["pre_purchase_rise_rate"]
    trend = judge_sustained_rise(state["forecast"], constraints)

    def ref(kind: str) -> tuple[str, ...]:
        return (f"{item}-{kind}-{as_of}",)

    out = [
        Evidence(
            claim="situation",
            source="tool_calc",
            ref_ids=ref("CI"),
            value=round(ci_width, 6),
            unit="ratio",
            evidence_grade="SIM_FIXED",
            evidence_detail=(
                f"D+{judgment_day} 구간폭을 임계 {threshold}와 비교해 "
                f"{_situation_josa(payload.get('situation'))} 판정"
            ),
        ),
        # ── allowed_axes는 게이트마다 한 건이다 (현서님 회신 8/27) ──────────
        #
        # "열린 축 개수"(2.0)를 싣지 않는다. 그건 답의 길이를 세어 답이라고 적은
        # 것이라 감사 가치가 없다. 나중에 "왜 그날 timing이 열렸나"를 보는 사람에게
        # 2.0은 아무것도 말하지 않는다. ``Evidence.value``의 용도는 판정을 만든 근거
        # 수치다.
        #
        # 축을 여닫는 게이트마다 근거가 있다. 하나로 합치면 나머지 수치가 사라진다.
        Evidence(
            claim="allowed_axes",
            source="tool_calc",
            # ``situation``과 같은 ``ref_id``다. §4.2.2가 "하나의 신뢰도 판정이
            # 개수·허용 축·분할 진입 셋을 동시에 결정한다"로 정했으므로 판정이 하나면
            # 근거도 하나다 — 추적하면 한 곳으로 모인다.
            ref_ids=ref("CI"),
            value=round(ci_width, 6),
            unit="ratio",
            evidence_grade="SIM_FIXED",
            # "→ 허용 축 [...]"이라고 쓰지 않는다. 구간폭이 정하는 것은 ``situation``
            # 이고, 축에 대해서는 선매입 궤적 조건(by_trend)만 연다·닫는다.
            # timing은 총량 게이트로도 열리므로(아래 VOL 근거), uncertain인데 timing이
            # 열린 날이 실재한다 — 그때 이 문장이 "uncertain → timing 열림"으로 읽히면
            # 없는 인과를 주장하게 된다.
            evidence_detail=(
                f"구간폭 {ci_width:.3f} {_relation(ci_width, threshold, comparison)} {threshold}"
                f" → {_situation_ko(payload.get('situation'))} → 선매입 궤적 "
                f"{'차단' if payload.get('situation') == 'uncertain' else '허용'}"
            ),
        ),
        Evidence(
            claim="allowed_axes",
            source="tool_calc",
            # 총량 게이트다. 현서님 회신이 "축을 닫는 다른 게이트가 있다면 그
            # 게이트의 값을 쓰는 게 맞다 — 그런 게이트가 있습니까?"라고 물었는데,
            # 있다: timing은 ``by_volume OR by_trend``로 열리고 by_volume은 situation과
            # 무관하다. 이 근거가 없으면 uncertain인데 timing이 열린 날을 설명할 수 없다.
            ref_ids=ref("VOL"),
            # 문장과 같은 수여야 한다 — ``_volume_gate_sentence`` 참조.
            value=float(ceil(estimated_total_kg)),
            unit="kg",
            evidence_grade="SIM_FIXED",
            # 세 갈래다 — 「못 봤다」를 「미달」로 적지 않는다 (규칙 3 · `#308`).
            # 여유를 못 받은 날에 "미달" 이라고 쓰면 판정하지 않은 것이 판정한 것으로
            # 읽히고, 읽는 사람은 그날 축이 왜 닫혔는지 되물을 수 없다.
            evidence_detail=_volume_gate_sentence(estimated_total_kg, arrival_cap),
        ),
        Evidence(
            claim="allowed_axes",
            source="tool_calc",
            # 가격 경로의 상승률 · 궤적 게이트다. 이 근거가 없으면 안정인 날 timing 이
            # 닫혀도 «상승률이 모자랐나 · 예측이 내려갔나 · 판정을 못 했나» 를 기록에서
            # 가를 수 없다. stable 여부는 위 CI 근거가 말한다 — 여기서 겹쳐 적지 않는다.
            ref_ids=ref("TREND"),
            value=round(rise_rate, 6),
            unit="ratio",
            evidence_grade="SIM_FIXED",
            evidence_detail=(
                f"D+{judgment_day} 예측 상승률 {rise_rate:+.1%} "
                f"{_relation(rise_rate, rise_threshold, '>=')} 임계 {rise_threshold:.0%} · "
                + sustained_rise_sentence(
                    trend.verdict, trend.withheld_reason, trend.first_decline
                )
            ),
        ),
        Evidence(
            claim="allowed_axes",
            source="tool_calc",
            # 편중은 다른 게이트라 ref_id도 다르다. 신뢰도와 같은 id를 쓰면
            # 서로 다른 두 판정이 한 근거를 가리키게 된다.
            ref_ids=ref("MIX"),
            value=round(top_mix_ratio, 6),
            unit="ratio",
            evidence_grade="SIM_FIXED",
            # 열린 날에도 싣는다. 닫힘만 기록하면 "왜 열렸나"의 근거가 없어지고,
            # 편중이 완화돼 mix가 부활한 날을 설명할 수 없다.
            # ⑤ 게이트 조건은 ``max(ratios) < threshold``면 개방이다. 부등호를 그 조건에서
            # 이끌어내 경계값에서도 참인 문장이 되게 한다 — 0.70에서 "0.700 > 0.7"
            # 이라고 적으면 판정은 맞고 문장만 거짓이 된다.
            evidence_detail=(
                f"품목 편중 최대 {top_mix_ratio:.3f} "
                f"{'<' if top_mix_ratio < mix_threshold else '≥'} {mix_threshold} → "
                f"등급 구성 {'개방' if 'mix' in axes else '제외'}"
            ),
        ),
        Evidence(
            claim="scenarios",
            source="tool_calc",
            ref_ids=ref("SCEN"),
            value=float(len(payload.get("scenarios") or [])),
            unit="count",
            evidence_grade="SIM_FIXED",
            # 개수는 독립 파라미터가 아니라 상황 판정의 파생값이다 (변경요청 1).
            evidence_detail=(
                f"{_situation_ko(payload.get('situation'))} 판정에서 파생 — "
                "예측이 불확실하면 공격안을 만들지 않아 두 안이 된다"
            ),
        ),
        *_scenario_evidences(payload, ref),
    ]


    # 아래 둘은 비어 있으면 근거를 요구받지 않는다 (빈 Sequence는 대상 밖).
    # 그런데도 붙이면 넘치는 쪽이라 ORPHAN은 아니지만 의미 없는 근거가 된다.
    if payload.get("context_docs_used"):
        out.append(
            Evidence(
                claim="context_docs_used",
                source="documents",
                ref_ids=tuple(payload["context_docs_used"]),
                value=float(len(payload["context_docs_used"])),
                unit="count",
                evidence_grade="SIM_FIXED",
                evidence_detail=(
                    # ⑥ ``context_risks`` 와 같은 사실이다. 한쪽만 고치면 화면과
                    # 봉투가 다른 말을 한다 — 문면을 바꿀 때 둘을 같이 본다.
                    "정해진 우선순위 순서대로 읽었다 — 어느 문서가 더 맞는지도, "
                    "이만하면 충분한지도 판정하지 않았다"
                ),
            )
        )
    if payload.get("rejected_reasons"):
        out.append(
            Evidence(
                claim="rejected_reasons",
                source="tool_calc",
                ref_ids=ref("CUT"),
                value=float(len(payload["rejected_reasons"])),
                unit="count",
                evidence_grade="SIM_FIXED",
                evidence_detail="자기 검증에서 컷된 안의 수 — 사유는 항목마다 실려 있다",
            )
        )
    return tuple(out)


#: 안 안쪽에서 근거를 요구받는 숫자와 그 값이 어디서 왔는지.
#: 봉투 v0.4가 배열을 한 겹 파고들어 ``scenarios[i].<필드>`` 경로로 요구한다
#: (M-1 §7.1 — 매입 요청으로 신설된 규칙이다. 재무 payload는 평면이라 1:1이 성립하지만
#: 우리는 같은 이름의 필드가 안마다 2~3벌이라 위치가 필요하다).
#:
#: 라벨은 면제다 — ``label``·``strategy_type``까지 요구하면 안마다 근거를 만들어야
#: 해서 과하다. 숫자만 다르다: 어디서 왔는지 없으면 LLM이 만든 값과 구분되지 않는다.
_SCENARIO_NUMERIC_SOURCES: dict[str, str] = {
    "coverage_days": "안별 커버일수 설정",
    # 이 문장이 남에게 나간다. "일평균 확정수요 × 커버일수" 라고만 적으면 마스터가
    # 그대로 읽어 ``total_qty_kg ÷ coverage_days`` 로 일수요를 되잡는다 — 그렇게 하면
    # 배추가 359 로 나온다 (정본 717.3 · 2026-09-12 회신). 차감을 빼고 적으면 남의
    # 계산이 반이 된다.
    "total_qty_kg": (
        "일평균 확정수요 × 커버일수에서 **보유 재고를 뺀** 양, 그 뒤 하드 제약"
        "(창고·현금·신선도)의 상한에 맞춰 줄임 — 이 값으로 일수요를 되잡지 말 것"
    ),
    "total_amount_krw": "등급별 수량 × 단가의 합 — 등급 배분에서 파생",
    "max_price": "커버 구간 예측 상단의 최대값 — 재무 STRESS 로 나간다",
    "cut_unit_price": "커버 구간 예측 상단의 최대값 — 매입 컷 기준 (STRESS 상한과 지금은 같다)",
    "expected_margin_rate": "(계약단가 − 가중 매입단가) ÷ 계약단가",
}


#: ``situation`` 의 화면 표기. 계약 값(``stable``/``uncertain``)은 그대로 두고
#: 사람이 읽는 문장에서만 이 표기를 쓴다 — 근거는 H1 화면과 Critic 이 읽는다.
#: 주의: 여기서 값을 바꾸면 안 된다. `payload["situation"]` 은 계약이고 이건 표기일 뿐이다.
_SITUATION_KO: dict[str, str] = {
    "stable": "예측이 안정적",
    "uncertain": "예측이 불확실",
}


def _situation_josa(situation: Any) -> str:
    """``_situation_ko`` 에 ``으로``/``로`` 를 붙여 돌려준다.

    조사를 문자열에 고정할 수 없다. 같은 자리에 두 값이 들어오는데 받침이 다르다 —
    "안정적" 은 ㄱ 받침이라 「안정적으로」 이고 "불확실" 은 ㄹ 받침이라 「불확실로」 다.
    ``…으로`` 로 박으면 uncertain 인 날마다 "예측이 불확실으로 판정" 이 나간다
    (12-31 관통 4품목 중 3품목 · 2026-09-03 실측).

    받침이 없거나 ㄹ 이면 ``로``, 그 밖의 받침이면 ``으로`` — 한글 음절이 아니면
    (``_situation_ko`` 가 모르는 값을 그대로 흘리는 경우) ``로`` 로 둔다.
    """
    word = _situation_ko(situation)
    if not word:
        return "로"
    code = ord(word[-1])
    if not 0xAC00 <= code <= 0xD7A3:
        return f"{word}로"
    jongseong = (code - 0xAC00) % 28
    return f"{word}로" if jongseong in (0, 8) else f"{word}으로"


def _situation_ko(situation: Any) -> str:
    """근거 문장에 쓰는 표기. 모르는 값이면 그대로 둔다 (숨기지 않는다)."""
    return _SITUATION_KO.get(str(situation), str(situation))


def _scenario_evidences(payload: Mapping[str, Any], ref: Any) -> list[Evidence]:
    """안별 숫자 근거. 경로 표기로 어느 안의 값인지 가리킨다.

    ``scenarios[0].total_amount_krw`` 형태다. 번호 대신 ``scenarios[공격]``처럼 이름으로도
    가리킬 수 있지만(봉투 ``canonical_claim``), 번호를 쓴다 — 라벨은 안 구성이 바뀌면
    사라질 수 있고 번호는 배열이 있는 한 항상 유효하다.

    ``None``인 값은 건너뛴다. ``expected_margin_rate``는 ``contract_price`` 미수령이면
    ``null``로 나가는데(IO명세 §2 동기화 규칙), 그때 봉투는 근거를 요구하지 않는다.

    주의: 봉투가 막아 주지는 않는다. "없는 값에 근거를 붙이면 고아 근거가 된다" 가
    아니다 — ``canonical_claim``은 값이 ``None``이어도 필드가 존재하면 경로를 인정하므로,
    여기서 ``0.0``을 지어내 붙여도 ``validate_reply``는 깨끗하다 (강제 삽입으로 재현).
    즉 미결을 0으로 채우지 않는 것은 이 ``continue`` 한 줄이 유일한 방어이고, 그래서
    테스트로 따로 잠근다.
    """
    out: list[Evidence] = []
    for index, scenario in enumerate(payload.get("scenarios") or []):
        for field, origin in _SCENARIO_NUMERIC_SOURCES.items():
            value = scenario.get(field)
            if value is None:
                continue
            out.append(
                Evidence(
                    claim=f"scenarios[{index}].{field}",
                    source="tool_calc",
                    ref_ids=ref(f"SC{index}"),
                    value=float(value),
                    unit=_SCENARIO_UNITS[field],
                    evidence_grade="SIM_FIXED",
                    evidence_detail=f"{scenario.get('label')}안 — {origin}",
                )
            )
    return out


_SCENARIO_UNITS: dict[str, str] = {
    "coverage_days": "days",
    "total_qty_kg": "kg",
    "total_amount_krw": "KRW",
    "max_price": "KRW/kg",
    "cut_unit_price": "KRW/kg",
    "expected_margin_rate": "ratio",
}
