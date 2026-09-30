"""모든 부서가 함께 쓰는 기반층 — 설정 읽기, PostgreSQL 연결, 시계, 작은 값 변환.

```text
settings.py   .env 위치 · DB 접속 정보 · 연결 풀 크기 · 스키마 이름
              · 화면이 보는 실행과 기준일 (발표용 고정값 · 2026-09-29 `app/api/shown_run.py` 에서)
db.py         psycopg 연결 풀 — 준비 · 대여 · 반환 · 종료
clock.py      시간대(서울)와 벽시계 — 앱에서 벽시계를 읽는 유일한 자리 (2026-09-29)
text.py       금액 표시 문자열 · 숫자 칸의 Decimal 변환 (2026-09-29)
llm/          외부 LLM 호출 한 자리(providers) · 실행 골격 · `<PREFIX>_` 설정 읽기(runtime)
              (2026-09-30 BL-020 — 지시문 · 검증 · 부서 정책은 각 부서 `llm/`)
```

★ 부서 코드는 여기를 부르지만, 여기는 부서를 부르지 않는다 (`app.finance` · `app.master`
  등을 import 하지 않는다). 부서마다 다른 동작은 각 부서의 `db.py` 가 들고 있다.
"""
