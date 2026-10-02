"""마스터 표를 화면 · 보고서가 읽는 모양으로 엮는 조회. 쓰지 않는다.

SQL 은 `repository/` 에 있고, 여기서는 조회 연결을 빌려 그 결과를 응답 모델로 엮는다.
화면(`app/api`)은 마스터 repository 대신 여기를 부른다.

```text
approvals.py            현재 승인 · 확정 입고 약정 재조립
backfill_rules.py       실행 설정의 자동 승인 규칙
collection_events.py    수금 사건
console_runs.py         콘솔 실행 목록
cycle_runs.py           Critic 실행 이력
day_openings.py         개장 정본 · 앞질러 열린 날
decisions.py            결정 이력
forecast_gate.py        그날 ML 예측이 왔는가
history.py              실행 이력 · 매입안 보고서 · 번인 구간 응답
holiday_calendar.py     공휴일 축 (ml_calendar_days.holiday_nm)
inputs.py               마스터가 싣는 입력 3종 (예측 · 확정 주문 · 정책값)
ledger.py               걷기 마감 행 · 번인 실행
logistics_report.py     채팅 물류 보고서가 읽는 물류 사실
market_calendar.py      개장 축 (ml_calendar_days.is_open)
ml_batch_calendar.py    예측 배치 축 (ml_calendar_days.is_survey)
pending_transitions.py  미적용 전이 후보 (승인 행 · 원장 매입 ID)
procurement_boundary.py 그날 매입 판단이 받아 둔 경계
purchase_record.py      실매입 기록 · 그날 승인에 적힌 실매입 합계
purchase_tab.py         매입 탭이 읽는 저장된 실행 · 확정 매입 · 결정 · 품목, 실행 고르기
runs.py                 마스터 실행 이력 · 걷기 날짜별 집계
```
"""
