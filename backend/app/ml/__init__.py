"""ML 파트 — 농산물 가격 예측.

경락가·중도매가·소매가를 품목별로 1~18일 앞까지 예측해 매입 판단에 넘긴다.

2026-09-29 재구성 BL-017 에 계층으로 나눴다 (설계서 대응표 `ml/` 행).

    adapter.py        마스터 봉투 ↔ 질의응답 번역 (`ml_port`)
    router.py         HTTP `/ml/qa` (BL-019 에서 `app/api/ml/` 로)
    console_proxy.py  HTTP `/ml/console/*` 허용 목록 · 실패 변환 (BL-019 에서 `app/api/ml/` 로)
    ml_backend.py     ML 백엔드 HTTP 호출 (콘솔 전달 · 재학습 대기)
    config.py         운영 모델 이름 · 봉인 개봉 성능표 · 라벨 · KST
    schemas/          예측 설정 · 질의응답 요청·응답 · 그래프 상태
    domain/           개장일 → 달력일 변환 · 질문 해석 · 읽을 범위와 답 상태
    repository/       SQL (예측 전달표 · 질의응답 · 화면 예측 탭) — 받은 연결로
    readmodel/        조회 연결을 빌려 읽고 조립 (질의응답 읽기 · 답 · 예측 계약 · 예측 탭)
    service/          질의응답 그래프 · 예측 적재 (원본 창고 → 서비스 창고)
    llm/              질문 해석 (Gemini)

창고는 둘이다 — 서비스 창고(`DB_*`, `app/core/db.py::SERVICE_POOL`)와 원본 창고
(`ML_SOURCE_DB_*`, `app/core/db.py::ML_SOURCE_POOL`).
"""
