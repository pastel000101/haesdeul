"""매입 판단 · 계산 — 입력 → 출력만. DB · HTTP · LLM · 벽시계 · 기능 플래그를 부르지 않는다.

임계 · 좌표 같은 부서 선언(`constraints.yaml` — 단일 소스)은 여기서 읽지 않는다 —
부르는 쪽(service 노드 · 어댑터)이 `config.load_constraints()` 로 읽어 dict 로 넘긴다.
노드 이름과 같은 파일은 그 노드(`service/nodes/`)가 부르는 판정 · 계산이다.
"""
