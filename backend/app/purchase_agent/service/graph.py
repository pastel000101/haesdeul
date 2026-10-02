"""8노드 LangGraph 그래프 (상세설계 §4 의 7노드 + ⑧ 근거 자기 검토).

```
① classify_situation ─ stable ────────────────┐
                     └ uncertain → ② collect_context
                                              ▼
                                       ③ draft_plan
                                              ▼
                                       ④ split_plan
                                              ▼
                                     ⑤ allocate_sourcing
                                              ▼
                                    ⑥ package_scenarios
                                              ▼
                                       ⑦ self_check
                                              ▼
                                    ⑧ review_rationale → END
```

조건부 분기가 하나 있다 — stable한 날은 ② 문서 루프를 건너뛴다. "문서를 읽을지 말지부터가
판단"이라 원라인 파이프라인이 아니라 그래프인 것이다 (§4). ⑦ 뒤에 ⑧ ``review_rationale``
(근거 자기 검토)이 서서 노드는 여덟이다 (``NODES``).

되돌아오는 간선은 없고, 앞으로도 안 만든다 (E3-6 결정).

상세설계 §6 이 "quantity 위반 → ③부터 재실행 · sourcing 위반 → ⑤만 · timing 위반 →
④만" 이라는 축별 재진입을 그려 뒀는데, 그 입력을 보내는 상대가 없다.

```text
마스터가 통째로 다시 부른다   master/service/flow.py
                              runner.call("purchase", "GENERATE_SCENARIOS", …)
                              → 그래프는 ①부터 새로 돈다. 재진입은 마스터가 소유한다
보내는 쪽이 축을 안 보낸다     violated_axis · constraint · keep 은 저장소 어디에도 없다
                              오는 것은 "이 안의 이 항목을 이 값 이하로" 뿐이다
```

그 설계는 계약 요구가 아니라 채점 대비 심화였다 (§0 멘토링 반영 표 —
"복잡한 그래프, 상태 관리·롤백 경험이 채점 본체"). 없는 입력을 위해 배관을 짓지
않는다. 되살아나는 조건: 마스터가 «어느 축이 걸렸는지» 를 보내기 시작하는 날.

④는 조건부 간선이 아니라 무조건 지나는 노드다 (E3-3). 진입 판정을 간선으로 빼면
미진입 시 ④가 아예 안 돌아 "왜 분할이 없는가"라는 사실이 ⑥에 도달하지 못한다.
④가 항상 돌면서 판정 근거를 실어 보내고, ⑥이 그걸 risks에 고지한다 — timing 라벨인데
회차가 하나인 상태를 소비자가 추적할 수 있어야 한다.

단독 실행이 쓰는 T0 초기 상태 ``build_initial_state`` 도 여기 있다 — 포트 다섯을 부르는
실행 경로라 State 모델(`schemas/state.py`)과 떼어 둔다. 노드는 `service/nodes/`.
"""

from datetime import date
from functools import partial
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from app.purchase_agent import ports
from app.purchase_agent.config import load_constraints
from app.purchase_agent.llm.mix import MixSelector, make_mix_selector
from app.purchase_agent.llm.self_review import Reviewer, make_reviewer
from app.purchase_agent.llm.split_allocation import SplitAllocationSelector, make_split_selector
from app.purchase_agent.readmodel.quotes import QuoteSource
from app.purchase_agent.schemas.state import PurchaseAgentState
from app.purchase_agent.service.nodes.allocate_sourcing import allocate_sourcing
from app.purchase_agent.service.nodes.classify_situation import classify_situation
from app.purchase_agent.service.nodes.collect_context import collect_context
from app.purchase_agent.service.nodes.draft_plan import draft_plan
from app.purchase_agent.service.nodes.package_scenarios import package_scenarios
from app.purchase_agent.service.nodes.review_rationale import review_rationale
from app.purchase_agent.service.nodes.self_check import self_check
from app.purchase_agent.service.nodes.split_plan import split_plan
from app.purchase_agent.service.tracing import ToolRecorder, wrap

#: 노드 이름 → 함수. 순서가 §4 그래프의 ①~⑦, 그 뒤 ⑧과 같다.
#: LLM 을 쓰는 ④·⑤·⑧은 부분 적용으로 감싼다 — 판단자는 그래프가 조립할 때 한 번 만들어
#: 주입한다 (E3-2). 노드가 스스로 서비스를 만들면 테스트가 실 API를 타게 되고, 주입 지점도
#: 사라진다.
NODES = {
    "classify_situation": classify_situation,
    "collect_context": collect_context,
    "draft_plan": draft_plan,
    "split_plan": split_plan,
    "allocate_sourcing": allocate_sourcing,
    "package_scenarios": package_scenarios,
    "self_check": self_check,
    # ⑧ 은 ⑦ 뒤에 선다. 계산 검사가 끝나고 살아남은 안만 보고, 컷 권한은 없다.
    #   ⑦ 안에 섞지 않는 이유는 경계다 — 컷하는 함수 안에 컷 못 하는 판단을 두면
    #   나중에 누가 「이것도 컷하면 되지 않나」로 읽는다.
    "review_rationale": review_rationale,
}


def route_after_classify(state: PurchaseAgentState) -> Literal["collect_context", "draft_plan"]:
    """uncertain일 때만 ② 문서 루프로 간다 (§4-②: "stable한 날은 이 노드를 건너뛴다")."""
    return "collect_context" if state["situation"] == "uncertain" else "draft_plan"


def build_graph(
    *,
    selector: MixSelector | None = None,
    split_allocation_selector: SplitAllocationSelector | None = None,
    reviewer: Reviewer | None = None,
    recorder: ToolRecorder | None = None,
) -> Any:
    """8노드를 배선해 컴파일한다 (⑧ ``review_rationale`` 은 ⑦ 뒤 · E3-10).

    ``recorder``는 ``used_tools``를 만들 통과 기록기다 (M-1 §6). ``None``이면
    노드를 감싸지 않는다 — 어댑터를 거치지 않는 호출은 이 층 자체를 만나지 않는다.

    ``selector``는 ⑤의 등급 조합 판단자다 (E3-2). ``None``이면 여기서 만든다 —
    설정이 LLM을 껐거나 키·서버가 없으면 그 선택자가 규칙 기본안을 돌려주므로,
    팀원이 브랜치만 받아도 산출물이 그대로 나온다. 테스트는 가짜 선택자를 꽂는다.
    """
    mix_selector = selector or make_mix_selector()
    # ④ 배분 판단자도 조립 시 한 번 만든다 (⑤ 와 같은 방식). 기능 플래그가 꺼져
    #   있으면 노드가 아예 안 부르므로, 여기서 만드는 것은 비용이 아니다.
    split_selector = split_allocation_selector or make_split_selector()
    rationale_reviewer = reviewer or make_reviewer()
    builder = StateGraph(PurchaseAgentState)
    for name, node in NODES.items():
        if name == "allocate_sourcing":
            # 부분 적용 — LLM 선택자는 그래프 조립 시 한 번 만들어 주입한다.
            node = partial(allocate_sourcing, selector=mix_selector)
        elif name == "split_plan":
            node = partial(split_plan, selector=split_selector)
        elif name == "review_rationale":
            node = partial(review_rationale, reviewer=rationale_reviewer)
        builder.add_node(name, wrap(node, name, recorder))

    builder.add_edge(START, "classify_situation")
    builder.add_conditional_edges(
        "classify_situation",
        route_after_classify,
        {"collect_context": "collect_context", "draft_plan": "draft_plan"},
    )
    builder.add_edge("collect_context", "draft_plan")
    builder.add_edge("draft_plan", "split_plan")
    builder.add_edge("split_plan", "allocate_sourcing")
    builder.add_edge("allocate_sourcing", "package_scenarios")
    builder.add_edge("package_scenarios", "self_check")
    builder.add_edge("self_check", "review_rationale")
    builder.add_edge("review_rationale", END)
    return builder.compile()


def run_purchase_agent(
    item: str,
    as_of: date,
    *,
    feedback: dict | None = None,
    selector: MixSelector | None = None,
    quotes: QuoteSource | None = None,
) -> dict:
    """품목 하나에 대해 그래프를 한 번 돌리고 제안 JSON을 돌려준다.

    read-only다 (규칙 2) — DB에 아무것도 쓰지 않고 반환이 전부다.
    as_of는 주입받는다 (규칙 1) — 벽시계를 읽지 않으므로 과거 날짜로도 그대로 돈다.

    T1은 품목별로 이 그래프를 돌린 뒤 전사 시나리오로 조합한다(§4). 조합은 아직 범위 밖이다.

    ``quotes``는 등급별 시세 공급자다 (#70). ``None``이면 mock 이라 회귀 테스트 전량이
    DB 없이 그대로 돈다 — 스위트가 DB 에 묶이지 않는 근거가 이 기본값이다.
    """
    final_state = build_graph(selector=selector).invoke(
        build_initial_state(item, as_of, feedback=feedback, quotes=quotes)
    )
    return final_state["proposal"]


def build_initial_state(
    item: str,
    as_of: date,
    *,
    feedback: dict | None = None,
    quotes: QuoteSource | None = None,
    execution_calendar: dict | None = None,
) -> PurchaseAgentState:
    """T0 스냅샷을 만든다 — 포트 ①~⑤를 한 번씩 호출한다 (T0 only).

    ⑥ ``get_context_docs``는 여기서 부르지 않는다. 문서 포트만 ② collect_context가
    런타임에 호출하는 예외다 (정의서 §3.1.1 · 팀 확인 2026-08-25 · IO명세 §0).
    예외가 안전한 이유는 ``ports.get_context_docs`` docstring에 적어두었다.

    ``item_mix_ratio`` · ``contract_price`` · ``margin_defense_floor_rate``는 IO명세 §1의
    계약 포트 6개에 없다. 그래도 외부 입력이므로 ports를 거친다(규칙 2) —
    ``get_snapshot_extras``가 그 잠정 경계이고, 스냅샷 형식이 확정되면 거기서만 바뀐다.

    중간 산출 필드는 채우지 않는다 — 각 노드가 자기 몫을 반환한다. 다만 ``context_docs``와
    ``context_loop_count``는 stable 경로에서 ② 노드를 건너뛰므로 빈 값으로 시작해야 하고,
    ``rejected_reasons``는 어느 노드든 append할 수 있어야 하므로 빈 목록으로 둔다.
    ``coverage_days``·``situation`` 같은 값은 0이나 빈 문자열로 채우지 않는다 — 미결과
    확정된 값을 구분해야 하기 때문이다 (규칙 3).

    ``quotes``는 등급별 시세 공급자다 (#70). ``None``이면 mock 이고, 실데이터로 돌리려면
    ``readmodel/quotes.py`` 의 ``auction_quote_source()``를 넘긴다 — 환경변수가 아니라
    명시 주입이다. 시세만 주입 지점을 여는 이유: 나머지 다섯은 마스터 봉투가 실어
    보내거나(어댑터 경로) mock 이고, 시세만 매입 자기 도메인이라 우리가 직접 읽는다
    (정의서 §4.1).

    ``None`` 이 mock 으로 도는 것은 pytest 안에서만이다 (#228). 운영 경로에서 mock 포트를
    부르면 ``MockNotAllowed`` 로 막힌다 (``ports.py`` — ``PYTEST_CURRENT_TEST`` 또는
    ``sys.modules`` 로 판단). 실운영 등록은 ``master/registry/bootstrap.py`` 가 실 공급자를
    꽂는다 (#226 · ``partial(purchase_port, quotes=auction_quote_source())``).

    그래서 이 함수는 실운영 경로가 아니다. 어댑터 경로(``scenarios.build_state``)는
      마스터 봉투에서 State 를 만들고 시세 하나만 포트로 읽는다. 여기 다섯 포트를
      부르는 것은 ``run_purchase_agent`` — 단독 실행과 회귀 스위트의 경로다.
    """
    constraints = load_constraints()
    # 창·지평을 파라미터로 받지 않는다. 여기서 쓴 창과 ③이 나눌 창이 달라지면 수량이
    # 조용히 틀어지므로, 양쪽 모두 constraints.yaml 한 곳에서 읽는다 (규칙 7).
    order_days = constraints["demand"]["order_window_days"]
    cash_horizon_days = constraints["cash"]["horizon_days"]
    extras = ports.get_snapshot_extras(item, as_of)
    return {  # type: ignore[return-value]  # 중간·출력 필드는 노드가 채운다
        "date": as_of.isoformat(),
        "item": item,
        "forecast": ports.get_forecast(item, as_of),
        "market_quotes": ports.get_market_quotes(item, as_of, source=quotes),
        "inventory": ports.get_inventory(item, as_of),
        "confirmed_orders": ports.get_confirmed_orders(item, as_of, days=order_days),
        "item_mix_ratio": extras["item_mix_ratio"],
        "contract_price": extras["contract_price"],
        "margin_defense_floor_rate": extras["margin_defense_floor_rate"],
        "projected_cash_min": ports.get_projected_cash_min(as_of, cash_horizon_days),
        "feedback": feedback,
        # 포트로 안 받는다. 실행일 봉투는 부서가 낸 값이 아니라 마스터가 자기 달력에서
        #   만든 것이라 ``approved_commitments`` 와 같은 자리다 — 운영은 어댑터 경로
        #   (``scenarios.build_state``)가 봉투에서 읽고, 검사는 여기로 넣는다. 기본값이
        #   ``None`` 이고 그것은 «안 왔다» 다 (규칙 3) — 빈 목록으로 채우면 «안 서는 날이
        #   없다» 는 없는 사실이 생긴다.
        "execution_calendar": execution_calendar,
        "context_docs": [],
        "context_loop_count": 0,
        "rejected_reasons": [],
        "proposal": None,
    }
