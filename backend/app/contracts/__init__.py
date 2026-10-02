"""공용 계약 패키지.

한 파트의 소유가 아니다. 다섯 파트와 마스터가 대등하게 참조한다. 계약 타입이 한 파트의
폴더 안에 있으면 나머지 파트가 모두 그 파트를 임포트하는 모양이 되어 폴더 이름이 의존
관계를 잘못 말한다. 그래서 봉투 · 약정 타입 · 파트 결과 · 예측 계약을 여기에 둔다.

    core.py              타입 · 어휘 · 상수 · 계약 위반 예외
    rules.py             여러 파트가 같이 쓰는 판정·계산 (core.py 타입으로)
    envelope.py          마스터 ↔ 에이전트 봉투 — 타입 · 어휘 · 라우팅 표 · 직렬화 · 회신 검사
    commitment.py        승인 매입의 확정 입고 약정
    parts.py             하루 단계 파트 결과 (입고 · 마감 · 수금 · 채권 발행)
    forecast.py          ML 가격 예측
    aging.py             매출채권 연체 구간
    receivable_history.py  기준일 시점 매출채권 상태 규칙 (재무 · 판매 공용)
    sales_logistics.py   판매 → 물류 출고 예약 요청

제약: `app` 안에서는 `app.contracts` 와 `app.core` 만 import 한다. 파트·마스터·화면을
읽으면 그 순간 다시 한 파트의 폴더가 된다 (`tests/contracts/test_contracts_core_home.py`).

제약: DB · HTTP · LLM · 파일을 건드리지 않고 업무 흐름을 두지 않는다. 타입 모듈의 함수는
그 계약 자체를 정의하는 것까지다 — 생성 시 검사, 전선 직렬화, 회신 적합성 검사, 계약 표
조회. `core.py` 타입으로 하는 판정·계산은 `rules.py` 에 모은다. `aging.py` ·
`sales_logistics.py` 는 어휘와 그 분류 · ID 규칙 한 벌을 같이 둔 작은 계약이다.
시각은 기록 시각 기본값 두 칸(`CriticVerdict.decided_at` · `CycleLog.started_at` 의
`datetime.utcnow`)뿐이고 업무 날짜(`as_of`)는 읽지 않는다.
"""
