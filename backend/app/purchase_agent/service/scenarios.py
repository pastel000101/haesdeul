"""GENERATE_SCENARIOS — 봉투 payload 를 State 로 펴고 8노드 그래프를 한 번 돌린다.

```text
build_state          payload → State. 시세(자기 도메인)만 포트로 직접 읽는다
generate_scenarios   build_state → build_graph(recorder).invoke → 마친 State
```

수신 검사(`domain/payload.validate_payload`)는 어댑터가 먼저 한다 — 모자라면 여기까지
오지 않는다. 회신 · 근거 · 설명문을 만드는 것도 어댑터다. 순서는 State 먼저 → 그래프 조립
→ 실행이다.

DB 에 쓰지 않는다. 시세 조회 연결은 시세 공급자(`readmodel/quotes.py`)가 빌린다.
"""

from collections.abc import Mapping
from typing import Any

from app.contracts.envelope import AgentRequest
from app.purchase_agent import ports
from app.purchase_agent.domain.payload import absorb_inventory, approved_commitments
from app.purchase_agent.readmodel.quotes import QuoteSource
from app.purchase_agent.schemas.state import PurchaseAgentState
from app.purchase_agent.service.graph import build_graph
from app.purchase_agent.service.tracing import ToolRecorder


def build_state(request: AgentRequest, *, quotes: QuoteSource | None = None) -> PurchaseAgentState:
    """수신 payload를 그래프가 아는 State로 편다 (IO명세 §2-B).

    ``build_initial_state``와 다른 경로다 — 그쪽은 포트를 호출해 값을 당겨오고,
    이쪽은 이미 받은 값을 배치한다. 매입 자기 도메인(당일 시세·시장 문서)만 포트가
    그대로 담당하므로, 그 둘은 여기서도 ``ports``를 거친다.

    ``quotes``는 그 시세의 공급자다 (#70). ``None``이면 mock 이고, 실데이터로 돌리려면
    ``readmodel/quotes.py`` 의 ``auction_quote_source()``를 넘긴다 — 환경변수가 아니라
    명시 주입이다.

    ``None`` 이 mock 으로 도는 것은 pytest 안에서만이다 (#228). 운영 경로에서 mock 포트를
    부르면 ``MockNotAllowed`` 로 막힌다 (``ports.py`` — ``PYTEST_CURRENT_TEST`` 또는
    ``sys.modules`` 로 판단). 실운영 등록은 ``master/registry/bootstrap.py`` 가 실 공급자를
    꽂는다 (#226 · ``partial(purchase_port, quotes=auction_quote_source())``).
    """
    payload = request.payload
    as_of = request.context.as_of
    finance: Mapping[str, Any] = payload["constraints"]["finance"]
    inventory: Mapping[str, Any] = payload["constraints"]["inventory"]
    policy: Mapping[str, Any] = payload["policy_values"]
    item = payload["item"]

    return {  # type: ignore[return-value]  # 중간·출력 필드는 노드가 채운다
        "date": as_of.isoformat(),
        "item": item,
        "forecast": dict(payload["forecast"]),
        # 자기 도메인 — 마스터를 거치지 않는다 (정의서 §4.1)
        "market_quotes": ports.get_market_quotes(item, as_of, source=quotes),
        "inventory": absorb_inventory(inventory, item),
        "confirmed_orders": dict(payload["confirmed_orders"]),
        "item_mix_ratio": dict(policy["item_mix_ratio"]),
        "contract_price": policy.get("contract_price_krw"),
        # 마진 방어선은 재무 Policy 소유라 policy_values가 아니라 재무 payload에 있다
        # (v2.3 M-19 해소 · 재무 회신 v2.2.1).
        # 참조값이라 없어도 돈다 — 어느 노드도 이 값을 쓰지 않는다.
        "margin_defense_floor_rate": finance.get("margin_defense_floor_rate"),
        "projected_cash_min": finance["base_projected_cash_min"],
        "finance_cap_amount_krw": finance.get("finance_cap_amount_krw"),
        "purchase_payment_days": finance.get("purchase_payment_days"),
        # N4는 물류 payload에 있다 (재무가 아니다). ``absorb_inventory``가 통째로
        # 복사해 ``state["inventory"]`` 안에도 들어가지만, ``pending_value``는 State
        # 최상위를 보므로 여기서 한 번 더 올려야 값이 실제로 쓰인다.
        # ``or``를 쓰지 않는다 — 0은 "당일 도착"이라는 확정된 값이라 폴백 대상이 아니다 (규칙 3).
        "inbound_lead_days": inventory.get("inbound_lead_days"),
        "critical_payment_dates": list(finance.get("critical_payment_dates") or []),
        # 재무 봉투 열 칸 중 다섯만 읽는다. 안 읽는 다섯을 여기 적어 둔다 (`#630`).
        #
        #   ```text
        #   읽는다 (5)     base_projected_cash_min · finance_cap_amount_krw ·
        #                  margin_defense_floor_rate · purchase_payment_days ·
        #                  critical_payment_dates
        #   안 읽는다 (5)  payment_pressure · available_cash ·
        #                  minimum_cash_balance_krw · payroll_payment_day ·
        #                  policy_version_used
        #   ```
        #
        #   다섯 중 하나만 계약이 「우리 행동을 바꾼다」고 적고 있다 —
        #     ``contracts/envelope.py`` 의 ``_is_label`` docstring 이
        #     "`payment_pressure: "MEDIUM"` 은 숫자가 아니지만 매입의 행동을 바꾼다"
        #     라고 이름 걸고 적어 뒀는데, 매입 코드에서 그 칸을 읽는 곳은 없다.
        #
        # 그래도 지금은 안 읽는다 (`#630` 갈래 ㉢).
        #
        #   재무가 정의를 확인해 줬다 — 그 칸은 "거래처 여신 노출도를 직접 평가하는
        #   신호가 아니다". 그리고 읽었어도 그 열흘을 안 막았다: 실측에서 그 구간
        #   내내 ``LOW`` 였다. 지금 읽으면 판정이 바뀌는데, 바뀔 근거가 없다.
        #
        #   읽을 근거가 생기는 조건: ``payment_pressure`` 가 ``LOW`` 가 아닌 날이 실행에
        #     나타나면 그때 연다. 그날이 오기 전까지 읽는 것은 «압력이 늘 낮은 축» 을
        #     판정에 얹는 것이다.
        #
        # 여신·미수금 칸은 열 칸 중 0개다. 그건 우리가 안 읽는 것이 아니라 봉투에 없는
        #   것이다 (마스터 여신 통보 §7). 「AR 이 아무 곳에도 안 들어간다」는 아니다 —
        #   수금은 재무 현금 투영에 들어가고, 봉투에 없는 것은 그 칸이다.
        "feedback": dict(payload.get("prior_feedback") or {}) or None,
        # ``or {}`` 로 접지 않는다. 마스터는 지평을 다 못 덮으면 봉투를 통째로
        #   안 싣고 그 사유를 자기 ``skipped_checks`` 에 남긴다. 여기서 빈 dict 로
        #   메우면 «안 서는 날이 없다» 는 없는 사실이 되고, ⑦ 이 «검사했다» 로 지난다.
        #   ``None`` 이면 ⑦ 이 미검사로 고지한다 (규칙 3 · `#300`).
        "execution_calendar": payload.get("execution_calendar"),
        # ``feedback`` 과 다른 슬롯이다 (되먹임 계약 v0.2 §2 · ``schemas/state.py`` 주석 참조).
        #   저쪽은 사람이 준 조건이고 이쪽은 조언자가 준 조정안이다 — 수명·모양·권위가
        #   달라 한 칸에 담으면 받는 쪽이 타입으로 갈라야 한다.
        #
        # 받아서 반영까지 한다 — 읽는 자리가 셋이다.
        #
        #       ③ draft_plan.split_adjustments  쓸 수 있는 것을 골라 ``adjustment_cap_kg``
        #                                       로 kg 환산해 ``caps`` 에 건다 — 수량 상한
        #       ⑥ package_scenarios.adjustment_risks  못 쓴 것을 사유와 함께 risks 에
        #       ⑦ self_check._assemble                 받은 건수를 meta.received_adjustments 에
        #
        #   (③ ⑥ 의 함수는 ``domain/draft_plan.py`` · ``domain/package_scenarios.py``,
        #   ⑦ 은 ``service/nodes/self_check.py`` 에 있다.)
        #
        #   ③ 만 반영이고 ⑥⑦ 은 고지다. 셋을 한 낱말로 묶지 않는다 — 묶으면 "받았다" 와
        #     "썼다" 가 같아지고, 보내는 쪽이 자기 제안이 수량을 움직인 줄 안다. 반영과
        #     독해도 다른 말이다. 뭉치면 "값을 실어 주고 안 쓰는" 자리가 다시 열린다.
        #
        #   ``target_value`` 는 상한이다 — 마스터 IO Contract §4.4 가 "그 값 이하" 로
        #     확정했고, ``adjustment_cap_kg`` docstring 이 그 조항을 들고 있다.
        #
        # 주의: ``schemas/state.py`` 에도 같은 설명(거른다/반영한다/말한다)이 있다. 한 사실을
        #   두 곳에 적으면 한쪽만 고쳐지는 날이 온다. 고칠 때 둘 다 연다.
        "adjustments": [dict(item) for item in payload.get("adjustments") or []],
        "feedback_context": dict(payload.get("feedback_context") or {}) or None,
        # 바로 위와 달리 ``or []`` 로 접지 않는다 (`#310` · 마스터 `#312`).
        #   마스터가 "없으면 칸을 안 만든다 — 빈 배열은 «어제 승인이 없었다» 와
        #   «마스터가 안 보낸다» 를 구별할 수 없다" 로 보내는 값이라, 여기서 ``[]`` 로
        #   접으면 보내는 쪽이 지킨 구분이 받는 쪽에서 사라진다 (규칙 3).
        #
        # 온 그대로 나른다. 마스터가 승인 이력을 해석하지 않고 실어 보내듯
        #   (``master/service/flow.py``), 우리도 여기서 고르거나 접지 않는다.
        "approved_commitments": approved_commitments(payload),
        "context_docs": [],
        "context_loop_count": 0,
        "rejected_reasons": [],
        "proposal": None,
    }


def generate_scenarios(
    request: AgentRequest,
    *,
    quotes: QuoteSource | None = None,
    recorder: ToolRecorder | None = None,
) -> dict[str, Any]:
    """payload 를 State 로 펴 그래프를 한 번 돌리고 마친 State 를 돌려준다.

    ``recorder`` 는 노드 통과 기록기다 — 어댑터가 실행 메타데이터의 ``used_tools`` 로 옮긴다.
    ``None`` 이면 노드를 감싸지 않는다 (``build_graph``).
    """
    state = build_state(request, quotes=quotes)
    return build_graph(recorder=recorder).invoke(state)
