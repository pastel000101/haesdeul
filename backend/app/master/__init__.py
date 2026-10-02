"""마스터 에이전트 (정의서 v2.2 · 소유: 이현서).

마스터 ↔ 도메인 에이전트가 주고받는 공용 계약(봉투, M-1 공통 이벤트 규약 v0.2)은
`app/contracts/envelope.py` 에 있다. 파트가 봉투를 쓰려고 마스터를 import 하지 않게 하려는
것이라, 이 패키지는 봉투 이름을 다시 내보내지 않는다.

이 패키지는 하위 모듈의 이름도 다시 내보내지 않는다. 여기서 본체 이름을 다시 내보내면
`app.master.X` 하나를 import 해도 Flow 전체가 로드된다. 쓰는 쪽은 정의된 자리에서 들인다.

    schemas/     요청 · 응답 · 결과 모델과 어휘 (다른 계층을 import 하지 않는다)
    domain/      입력 → 출력 판단 · 계산 (DB · HTTP · LLM · 파일 · 환경변수 · 시계 없음)
    service/     부서 호출 순서(Flow · runner · budget), 업무 실행, 연결 대여 · commit · rollback
    repository/  받은 연결로 SQL
    readmodel/   조회 연결 대여 + 조회 결과 조립
    registry/    포트 등록소(ports · wiring) · 하루 단계 파트 등록소 · 실행 축 묶기 ·
                 조립 뿌리(bootstrap)
    adapters/    등록소 Protocol 을 구현하는 재무 파트 표면 · Critic 계약 번역
    report/      매입안 Markdown · 채팅 보고서 · 걷기 성적표 · 걷기 요약
    cli/         하루 시뮬레이션 · 범위 걷기 · 자동 승인 채우기 · 실행 열기의 인자 · 출력
    llm/ · critic/ · cycle_llm/   지시문 · 스키마 · 검증기 · 설정값. 프로바이더 호출과 실행
                                   골격은 `app/core/llm/` 에 있다.

HTTP `/master/*` · `/critic/*` 라우트는 `app/api/master/` · `app/api/critic/` 에 있다 — 이
패키지에는 FastAPI 코드가 없다.
"""
