"""사이클 LLM 계약 — T3-5 선정의 입출력 스키마와 사이클 응답의 LLM 확장 필드.

`cycle_` 접두는 사이클 묶음을 한 이름 아래 모으려고 붙였다. 마스터의 Flow 골격과 섞이지
않게 한다.

이 패키지에는 `schemas.py` 하나만 있다. 선정을 실행하는 코드(runtime · selector)는 이
패키지에 없고, `schemas/cycle.py` 와 `app/logistics/schemas/agent.py` 의 응답 모델이
`LLMResponseFields` 를 상속한다."""
