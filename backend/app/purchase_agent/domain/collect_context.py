"""② collect_context — 문서 발췌 · 고르기 · 충분성 — 판정 · 계산 (입력 → 출력만).

노드 함수는 `service/nodes/collect_context.py` 에 있고, 여기 함수들을 순서대로 부른다.
다른 노드 · 어댑터 · 검사도 이 판정을 다시 부르므로 노드 밖에 둔다.
"""



#: 절단 표시. 발췌가 문장 경계가 아니라 글자 수로 잘렸다는 사실을 발췌 자체에 남긴다.
#: 현서님 2차 피드백의 반례가 이 상수의 존재 이유다 — "상승하지 않았다"가 상한에 걸려
#: "상승"으로 잘리면 부정이 사라진 완결된 주장이 되어 원문과 정반대로 읽힌다.
#: "상승…"이면 읽는 쪽이 문장이 끝나지 않았음을 안다.
TRUNCATION_MARK = "…"


def leading_excerpt(content: str, max_chars: int) -> tuple[str, bool]:
    """인용 발췌 — 규칙 단계에서는 본문 서두를 그대로 뜬다.

    현서님 합의 8/25 (IO명세 §0 P2): 문서를 근거로 쓰면 ``ref_id`` + 해당 구절을 출력에
    동봉한다. Critic은 DB 조회가 금지라 발췌 없이는 근거 대조 자체가 성립하지 않는다.

    어느 구절이 관련 있는가는 LLM 판단이다. 규칙은 그걸 못 하므로 하는 척하지 않고
    서두를 뜬다 — 원문 훼손이 없어 Critic 대조는 성립하고, "선별은 아직 없다"는 사실은
    ⑥(``package_scenarios``)이 risks에 적는다. LLM이 붙으면 이 함수 본문만 바뀐다.

    반환값 두 번째는 "글자 수로 잘렸는가"다. 접미사를 보고 되짚지 않는 이유는 원문이
    ``…``로 끝나는 경우와 구분할 수 없기 때문이다 — 잘랐다는 사실은 자른 쪽만 안다.
    ⑦(``self_check``)의 발췌 대조가 이 값으로 표시를 떼고 원문과 맞춘다.

    주의: 문장 경계 파서가 아니다. 한국어 종결이 "…다."라 그 첫 등장에서 자르지만,
    ``그는 '끝이다.'라고 말했다`` 같은 문장은 중간에서 잘린다. 못 찾으면 ``max_chars``까지
    자르는데 그건 문장이 아니라 그냥 앞부분이다. 그래서 함수 이름도 ``first_sentence``가
    아니고, ⑥의 risks 문구도 "첫 문장"이라고 주장하지 않는다.
    잘라내기만 하므로 원문 문자는 변조되지 않는다 — 대조는 어느 경우든 성립한다.

    빈 발췌는 돌려주지 않는다. 발췌 없는 인용은 Critic이 대조할 수 없어 "근거를 동봉했다"가
    거짓이 되므로, 만든 쪽에서 터뜨린다 — ``published_at`` 없는 문서를 로더가 적재 거부하는
    것과 같은 자리다 (IO명세 §1-⑥).
    """
    head, terminator, _ = content.partition("다.")
    if terminator:
        excerpt, truncated = head + terminator, False
    else:
        # 종결을 못 찾은 경로. 상한에 실제로 걸렸을 때만 잘렸다고 표시한다 —
        # 본문이 상한보다 짧으면 전문이 그대로 발췌이고, 거기 표시를 붙이면
        # 잘리지 않은 것을 잘렸다고 말하는 게 된다.
        excerpt, truncated = content[:max_chars], len(content) > max_chars
    # 빈 검사는 표시를 붙이기 전에 한다. 붙인 뒤에 보면 공백뿐인 본문도
    # 표시 한 글자 때문에 non-empty가 되어 이 방어가 통째로 무력해진다.
    if not excerpt.strip():
        raise ValueError("cannot build a citation excerpt from empty content; refusing load")
    return (excerpt + TRUNCATION_MARK if truncated else excerpt), truncated


def select_doc_types(constraints: dict) -> list[str]:
    """읽을 순서. constraints가 소유한다 (규칙 7) — 코드에 박지 않는다.

    §4-②의 고정 우선순위(관측월보 → 기상 → 작년 동기)를 그대로 쓴다.

    LLM 자리 후보였지만 «고르는 일»이 없어 LLM을 붙이지 않는다 (E3-8):

        loop_max(3) == len(doc_type_priority)(3)   순서와 무관하게 셋 다 읽는다
        ``loops``는 늘 0                            graph.py · service/scenarios.py
                                                    두 진입점 모두 0으로 시작
        ②로 돌아오는 간선이 없다                     service/graph.py
        무·양파는 관측월보 1종뿐                     나머지 회차는 어떤 순서로도 빈 결과

    그래서 순서가 바꾸는 것은 ``rationale`` 나열 순서 하나이고, 판정·수량·금액·안
    개수는 안 바뀐다. mock 5앵커에서 ②가 도는 날은 ``2026-09-04`` 하나이며(나머지는
    stable), 운영에서는 포트 ⑥ ``get_context_docs`` 가 ``MockNotAllowed``라 애초에
    문서가 0건이다 (E4-1).

    ``doc_type_priority``가 ``loop_max``보다 길어지는 날 이 판단을 다시 한다.
    그때 «무엇을 읽을까»가 처음 실재한다 —
    ``test_ordering_is_moot_while_the_list_fits_the_loop_budget`` 가 그날 실패한다.
    """
    return list(constraints["context"]["doc_type_priority"])


def is_enough(docs: list[dict]) -> bool:
    """"판단에 충분한가?" — 판정하지 않는다. 그것이 결정이다.

    LLM 자리 후보였지만 «규칙으로 간다»로 정했다 (E3-8).

    ⑤ 등급 조합은 LLM 을 붙였는데 여기는 안 붙이는 이유가 재료의 성질에 있다.
    ⑤는 규칙이 판정을 끝낸 뒤 라벨만 넘긴다 (``llm/mix.py`` — 숫자를 안 준다).
    충분성은 문서 본문을 봐야 판정되는데 관측월보는 숫자투성이라, 그 방어를
    본문에 적용할 방법이 지금 없다. 발췌를 그대로 주면 LLM 이 그 숫자를 사유에 베껴
    쓰고 그 순간 규칙 6 이 깨진다.

    항상 ``False``를 돌려 목록이 소진되거나 ``loop_max``에 닿을 때까지 계속 읽는다.
    ``docs``를 받지만 쓰지 않는 건 의도다 — 시그니처를 미리 맞춰두면 LLM이 붙을 때
    호출부가 그대로다. 조기 종료 규칙을 임의로 만들면(예: "1건이면 충분") 판정한 적 없는
    것을 판정한 것처럼 만들고, 9/4에서 DOC-4·5가 조용히 사라진다.
    """
    return False
