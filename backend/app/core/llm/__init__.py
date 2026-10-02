"""LLM 공용층 — 프로바이더 호출과 실행 골격.

```text
providers.py   외부 LLM 호출이 사는 유일한 자리
               — 요청 만들기 · 보내기 · 응답 읽기 · Gemini 스키마 변환
runtime.py     run_with_fallback · `<PREFIX>_` 우선 설정 읽기 · provider/모델 고르기
```

부서를 모른다(`app.core` 밖을 import 하지 않는다). 지시문 · 응답 스키마 · 검증기 · 부서별
설정값과 오류 문장 · 부서마다 다른 재시도 · 대체 정책은 각 부서 `llm/` 에 있다.

상태 네 값(`LLMStatus`)의 정의는 봉투 계약(`app/contracts/envelope.py`)이다. 골격은 같은
문자열을 낸다 — core 는 contracts 를 import 하지 않는다.
"""
