"""② collect_context — 문서 선택 로드 루프 (상세설계 §4-② · E3-4).

uncertain일 때만 돈다. 그래프의 조건부 분기가 stable한 날은 이 노드를 통째로 건너뛴다
(§4-②: "문서를 읽을지 말지부터가 판단이다").

검색 엔진은 없다. `doc_type`을 골라 선택 로드한다 — 코퍼스가 작아 전문을 통째로
주입한다(§2). 그래도 "상황에 따라 탐색 경로가 달라지는" agentic 구조는 루프가 유지한다.

포트 호출 위치의 유일한 예외가 여기다. ①~⑤는 T0(``build_initial_state``)에서 한 번씩
불리지만 ⑥ ``get_context_docs``는 이 노드가 런타임에 부른다 (정의서 §3.1.1 · 팀 확인
2026-08-25 · IO명세 §0). 문서는 ``published_at <= as_of``로 고정된 불변 발행물이라 사이클
중에 값이 변하지 않기 때문이다. 근거 전문은 ``ports.get_context_docs`` docstring에 있다.

LLM 자리 후보가 두 곳 있고, 둘 다 «규칙으로 간다»로 정했다 (E3-8 · 규칙 6):

1. "다음에 어떤 문서를 읽을까" → 우선순위 목록 순서   ``select_doc_types``
2. "판단에 충분한가?"       → 판정하지 않는다        ``is_enough``

«안 꽂는다»가 아니라 «지금은 꽂을 일이 없다고 판정했다»다. 근거와 되살아나는 조건은
각 함수 docstring 에 있고, 그 조건이 깨지는 날 ``test_context.py``가 운다.

2번을 "1건 찾으면 충분"으로 바꾸면 안 된다 — 규칙은 충분성을 판정할 수 없고, 조기 종료는
판정하지 않은 것을 판정한 것처럼 만든다. 그 사실은 ⑥이 risks에 고지한다.

이 파일에는 노드 함수(문서 포트를 부른다)가 있고, 판정 · 계산은 `domain/collect_context.py`
에 있다.
"""

from datetime import date
from typing import Any

from app.purchase_agent import ports
from app.purchase_agent.config import load_constraints
from app.purchase_agent.domain.collect_context import is_enough, leading_excerpt, select_doc_types
from app.purchase_agent.ports import MockNotAllowed
from app.purchase_agent.schemas.state import PurchaseAgentState


def collect_context(state: PurchaseAgentState) -> dict[str, Any]:
    """우선순위 순으로 문서를 선택 로드한다. 루프 상한은 ``context.loop_max``(3).

    탈출 조건은 세 가지다. 앞의 둘은 for 경계로 표현한다 — ``while``로 쓰면 "언젠가
    끝난다"가 조건문 안에 숨고, 조건이 틀리면 그래프가 멈춰 선다:

    - ``range(loop_max)``      : 재진입 상한
    - 목록 소진 시 ``break``   : 더 읽을 유형이 없다

    셋째는 for 경계가 아니다 — 루프 한가운데의 조기 ``return``이다.
    ``MockNotAllowed``면 남은 유형을 안 읽고 그 자리에서 나간다 (아래 ``except``).
    상한도 목록도 아직 남아 있는데 끝나므로, 위의 둘과 같은 줄에 적으면 거짓이 된다.
    ``context_unavailable``에 사유를 싣고 ⑦이 각 안의 risks로 올린다 — 컷하지 않는다.

    "충분 판정" 탈출은 없다. ``is_enough()``가 항상 ``False``다.

    같은 문서를 두 번 담지 않는다. 유형은 목록에서 소비하고(``pop``), 그래도 ``doc_id``로
    한 번 더 거른다. 한 유형에 여러 문서가 걸리는 날이 있어(9/11 배추 관측월보 = DOC-3·6)
    유형 소비만으로는 부족하다. 중복이 들어가면 ``context_docs_used``에 같은 DOC이 두 번
    실리고 rationale도 두 벌이 된다.

    ``published_at`` 필터를 여기서 다시 하지 않는다. 포트가 이미 한다
    (``mocks._load.filter_by_published_at``). 두 곳에 두면 한쪽만 바뀐다.

    빈 목록으로는 포트를 부르지 않는다 — ``doc_types=[]``는 ``ValueError``다.

    ``loop_max``는 누적 상한이다. ``state["context_loop_count"]``에서 이어 세고 남은
    예산만큼만 돈다 — §3이 이 필드를 "max 3"으로 규정하므로, 재진입해도 그 값을 넘으면
    안 된다. 지금 그래프엔 ②로 돌아오는 간선이 없어 도달 불가한 경로지만, 필드가 약속한
    불변을 코드가 아니라 배선이 지키게 두면 간선 하나 추가에 조용히 깨진다.
    """
    constraints = load_constraints()
    loop_max = constraints["context"]["loop_max"]
    excerpt_max_chars = constraints["context"]["excerpt_max_chars"]
    remaining = select_doc_types(constraints)

    # 벽시계를 읽지 않는다 (규칙 1). State에 실려 온 as_of만 포트에 넘긴다.
    as_of = date.fromisoformat(state["date"])
    collected: list[dict] = []
    seen: set[object] = set()
    loops = state["context_loop_count"]

    for _ in range(max(0, loop_max - loops)):
        if not remaining or is_enough(collected):
            break
        doc_type = remaining.pop(0)
        loops += 1
        # 요청한 유형에 문서가 없는 날도 정상이다 (무·양파엔 기상·작년동기 문서가
        # 없다). 빈 회차도 한 번의 시도로 세어야 "찾아봤지만 없었다"가 루프 수에 남는다.
        #
        # 문서를 못 읽으면 없이 진행한다 (마스터 결정: "문서 없으면 없이 진행하고,
        # 생기면 생긴대로 진행한다. 내가 통제한다."). 실 문서 소스가 아직 DB 에 없어
        # `get_context_docs` 가 mock 을 막으면(`MockNotAllowed`), 여기서 멈추지 않고
        # 빈 문서로 계속한다.
        #
        # mock 을 쓰는 게 아니다. 연습 데이터로 메우는 것과, 문서가 없어 없이 가는 것은
        # 다르다 — mock 은 여전히 안 쓴다. 못 읽었다는 사실만 남겨 ⑦ self_check 이 각 안의
        # risks 에 고지한다 (컷하지 않는다).
        #
        # "그날 그 유형이 없다"(무·양파)와 "읽으려다 못 읽었다"는 다르다 — 앞은 빈
        # `context_docs`, 뒤는 `context_unavailable` 에 남는다. 둘 다 안은 낸다.
        try:
            found = ports.get_context_docs(state["item"], as_of, [doc_type])
        except MockNotAllowed as blocked:
            return {
                "context_docs": collected,
                "context_loop_count": loops,
                "context_unavailable": str(blocked),
            }
        for doc in found:
            if doc["doc_id"] in seen:
                continue
            seen.add(doc["doc_id"])
            excerpt, truncated = leading_excerpt(doc["content"], excerpt_max_chars)
            collected.append(
                {**doc, "excerpt": excerpt, "excerpt_truncated": truncated}
            )

    return {"context_docs": collected, "context_loop_count": loops}
