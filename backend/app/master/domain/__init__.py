"""마스터의 순수 판정 — 입력 값만 보고 답한다. DB · HTTP · 화면 조립을 모른다.

판정 · 계산 모듈이 모여 있다 — 매입 · 판매 Flow 판정(`flow` · `sales_flow`), 밴드(`band`),
실행 계획(`plan`), 결정 규칙(`decision`), 검증 규칙(`verifier`), 실행일 · 시각
(`execution_day` · `sim_time` · `schedule_times`), 스케줄러 판단(`scheduler`), 매입안 상태
(`plan_state`) 등. 목록은 이 폴더가 정본이다.

DB · HTTP · LLM · 파일 · 환경 · 시계를 들이지 않는 것은 `tests/architecture/test_master_layers.py`
가 지킨다.
"""
