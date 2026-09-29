"""① classify_situation + compute_allowed_axes (계산, LLM 없음) — 상세설계 §4-①.

🟢 **자리 (2026-09-29 · 재구성 BL-016).** 전에는 `nodes/classify_situation.py` 였다. 노드 함수와
  비교 연산표만 남기고, 판정 · 계산은 `domain/classify_situation.py` 로 옮겼다.
"""

import operator
from collections.abc import Callable
from typing import Any

from app.purchase_agent.config import ci_width_threshold, load_constraints
from app.purchase_agent.domain.classify_situation import compute_allowed_axes, compute_ci_width
from app.purchase_agent.schemas.state import PurchaseAgentState

#: ``ci_width_comparison`` 문자열 → 실제 연산. 임계와 비교 방향을 둘 다 파일에서 읽어야
#: 한쪽만 바뀌었을 때 판정이 조용히 뒤집히지 않는다.
_COMPARISONS: dict[str, Callable[[float, float], bool]] = {">=": operator.ge, ">": operator.gt}


def classify_situation(state: PurchaseAgentState) -> dict[str, Any]:
    """신뢰구간 폭으로 stable/uncertain을 가르고, 그날 허용 축을 계산한다.

    🟢 **"이 예측을 써도 되나"를 먼저 묻는다** (#213 · 2026-09-04).

      ML 이 신뢰도 플래그 넷을 붙여 보내고 **넷 다 payload 에 온다**::

          use_recommended   조합(품목 × 계열)별   forecast 최상위    이 예측을 쓸 수 있나
          gate_reason       행(offset)별         daily 원소 안      왜 게이트됐나
          is_gated          행별                 daily 원소 안      출처 (모델 vs 어제 값)
          is_filled         행별                 daily 원소 안      그 날짜 예측이 없어 복사된 행

      층이 다르다 (#67 본문)::

          ML    use_recommended · gate_reason   "이 예측을 쓸 수 있나"   ← 앞
          매입  ci_width                        "얼마나 자신 있나"       ← 뒤

      **앞 질문은 어댑터가 한다** (``payload.validate_forecast``). 여기까지 온
      예측은 이미 *"써도 된다"* 가 확인된 것이라, 이 노드는 뒤 질문만 한다.

    🔴 **이 자리에 "셋 다 읽고 싶어도 못 읽는다" 고 적었었다 (2026-09-04 정정).**

      그때 적은 순서는 이랬다::

          ①  뷰       daily 에 gate_reason 을 더한다        ✅ #220 (09-03 19:20)
          ②  마스터   use_recommended 를 나른다             ✅ #208 (09-03 17:50)
          ③  매입     읽어서 판정 앞에 건다                  ← 이 판

      ①②는 **우리가 "못 읽는다"고 적던 그날 남이 이미 끝냈다.** 우리 정정 커밋이
      ②보다 19분 늦었고, 남이 고친 것을 안 보고 우리 판단을 옮겨 적었다.
      ⚠️ 그리고 ②의 처방도 틀렸었다 — 마스터는 ``_FORECAST_ENVELOPE_KEYS`` 가 아니라
      ``_forecast_payload`` 에 넣었다. 앞은 *"ML 봉투에서 내려보내는 필드"* 라
      **ML 이 안 보낸 키를 얹으면 받는 쪽이 ML 이 준 것으로 읽는다.**

    ⚠️ **지금은 아무것도 안 걸린다 — 우연이 아니라 우리가 AUC 만 보기 때문이다.**
      실측 3품목 × 7배치 = 21조합 (2026-09-04)::

          use_recommended = false   양파 × WHSL 하나뿐. AUC 는 21조합 다 true
          gate_reason = quality     WHSL 에만 101건. AUC 는 lead_time 75건뿐
          판정일(D+14) is_gated     21조합 다 false
          판정일(D+14) is_filled    21조합 다 false
                                    🔴 504조합에서는 27건이 true — 전부 공휴일이다 (#384)

      **계열이 늘거나 AUC 에 quality 가 생기는 날 자리가 이미 있다.** 값이 오고
      계산도 되니 에러가 안 나는 종류라, 그날 아무도 모르는 것이 원래 문제였다.

    ★ ``is_filled`` 는 **판정에 안 쓴다 — 다만 고지는 한다** (2026-09-07 정정 · ``#384``).

      전에는 *"판정일이 주(週)의 배수라 복사값을 안 밟는다"* 를 근거로 고지도 안 했다.
      🔴 **그 근거가 위 21조합에서만 참이었다.** 504조합으로 넓히니 D+14 에 **27건**이
      복사값이고, 전부 **``target_dt`` 가 공휴일**이다 (``base_dt`` 는 정상 개장일).

      ★ 주기 가정 자체는 살아 있다 — ``base_dt + 14`` 는 같은 요일이라 **주말**을
        안 밟는다. 다만 **공휴일은 요일과 무관하다.**

      ⚠️ 그래도 **판정에는 안 쓴다.** 복사값이라고 틀린 값이 아니고, ML 이 이 값으로
        무엇을 하라는 지시를 준 적도 없다. ⑥이 문장만 붙인다
        (``package_scenarios._judgment_day_risks`` · ``max_price`` 창은 별도로 ``forecast_risks``).
    """
    constraints = load_constraints()
    rules = constraints["situation"]
    # 🔴 **임계는 품목별이다** — 그래서 이 노드가 ``state["item"]`` 을 읽는다.
    #   전에는 안 읽었다. 품목이 판정에 안 들어가던 시절의 흔적이고, 임계가 갈리는
    #   순간부터는 **어느 품목의 임계인지**가 판정의 일부다.
    #   없으면 기본값으로 안 떨어지고 멈춘다 (``ThresholdNotDeclared`` · 규칙 3).
    #   여기까지 온 요청은 어댑터가 문 앞에서 같은 조건을 이미 봤다
    #   (``payload.validate_payload`` → ``RUNTIME_NOT_READY``) — 이 줄은 그 뒤의
    #   백스톱이다. mock 경로(``build_initial_state``)는 문을 안 지나므로 여기서 처음 걸린다.
    threshold = ci_width_threshold(state["item"], constraints)
    ci_width = compute_ci_width(state["forecast"], rules["ci_judgment_day"])
    exceeds = _COMPARISONS[rules["ci_width_comparison"]]
    situation = "uncertain" if exceeds(ci_width, threshold) else "stable"
    return {
        "situation": situation,
        "allowed_axes": compute_allowed_axes(state, situation, constraints),
    }
