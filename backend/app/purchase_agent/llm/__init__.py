"""매입 에이전트의 LLM 경계 (E3-2 · 상세설계 §4-⑤ E3-2 확정 블록).

노드는 이 패키지 안쪽을 모른다. 노드가 아는 표면은 역할마다 하나씩인 콜러블이다 —
⑤ ``mix.make_mix_selector()`` · ④ ``split_allocation.make_split_selector()`` ·
⑧ ``self_review.make_reviewer()``. 설정·프로바이더·검증·재시도·fallback은 전부 여기서
끝난다. critic의 ``master/critic/llm/judge.py``와 같은 배치다.

프로바이더 호출과 재시도 · fallback 골격은 공용층 ``app/core/llm/``이다. 여기 있는 것은
매입의 몫이다 — 지시문 · 응답 스키마 · 검증기 · ``PURCHASE_`` 설정값. 규약(환경변수
이름·status 4값·Provider 프로토콜·temperature 0·숫자 금지 검증)은 팀 공통이다 — §4-⑤
E3-2 확정 블록의 마지막 줄이 그 뜻이다.
"""
