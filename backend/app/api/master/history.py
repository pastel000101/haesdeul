"""실행 이력 · 보고서 조회 — `GET /master/runs/{request_id}[/report]` · `/master/burn-in` ·
`/master/walks/{sim_run_id}/report`.

HTTP 만: 조회는 `master/readmodel/history.py`, 걷기 성적표는 `master/report/walk_report.py`(공휴일
달력은 `master/readmodel/holiday_calendar.py`).
"""

from datetime import date

from fastapi import APIRouter, HTTPException, status

from app.master.readmodel.history import get_burn_in_history, get_run_history, get_run_report
from app.master.readmodel.holiday_calendar import get_calendar
from app.master.report.walk_report import walk_report as build_walk_report
from app.master.schemas.history import BurnInOut, ReportOut, RunHistoryOut
from app.master.schemas.walk_report import WalkReport

router = APIRouter(prefix="/master", tags=["master"])


@router.get(
    "/runs/{request_id}",
    response_model=RunHistoryOut,
    summary="실행 이력 조회 — 그 요청이 어떻게 됐나",
)
def master_run_history(request_id: str) -> RunHistoryOut:
    """업무 키(`REQ-20260827-0001`)로 실행 계획과 요청 · 응답 원문을 돌려준다.

    검증 Tool 의 실행 계획 온전성 검사(M-16)가 읽는 경로이기도 하다. `plan` 은 응답
    원문 안이 아니라 별도 컬럼에서 오므로, 응답 스키마가 바뀌어도 검증이 영향을 받지
    않는다.
    """
    try:
        return get_run_history(request_id)
    except LookupError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error


@router.get(
    "/runs/{request_id}/report",
    response_model=ReportOut,
    summary="매입안 보고서 — 들고 나갈 수 있는 Markdown",
)
def master_run_report(request_id: str) -> ReportOut:
    """안마다 분할 · 조달 · 지급 일정과 근거를 정리한 문서.

    지적 · 확인 필요 · 실행하지 못한 검사 · 입력 출처를 각 안 옆에 같이 싣는다. 결론만
    있으면 문서를 받은 사람이 그 숫자를 어떻게 읽어야 하는지 알 수 없기 때문이다.
    """
    try:
        return get_run_report(request_id)
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.get(
    "/burn-in",
    response_model=BurnInOut,
    summary="번인 구간 — 에이전트가 판단하기 전에 회사가 어떻게 왔나",
)
def master_burn_in() -> BurnInOut:
    """`sim_runs` 의 `BURN_IN` 한 건과 일별 마감 30일.

    에이전트가 판단하기 전 구간을 결론 옆에 보여 주기 위한 경로다. 예를 들어 에이전트가
    12-31 에 "살 안이 없다" 고 답할 때, 그 앞 30일을 같이 보지 않으면 시스템이 고장 난
    것처럼 읽힌다.

    읽기 전용이다. 하루를 진행시키지 않는다. 번인 실행이 없으면 404 다.
    """
    try:
        return get_burn_in_history()
    except LookupError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error


@router.get(
    "/walks/{sim_run_id}/report",
    response_model=WalkReport,
    summary="걷기 성적표 — 날마다 실행 여부를 판정한다",
)
def master_walk_report(sim_run_id: str, start: date, end: date) -> WalkReport:
    """한 걷기(하루 단위 시뮬레이션 실행)가 범위 안에서 어떻게 진행됐는지 날마다 한 줄로
    돌려준다. 실행 이력이 없는 날도 한 줄이다.

    실행 이력이 없는 날을 두 가지로 가른다. 범위를 받아 빈 날을 계산하고, 그날이
    실행일인지로 뜻을 정한다.

    | 판정 | 뜻 |
    |---|---|
    | `WALKED` | 실행했다 |
    | `SKIPPED_OFF_DAY` | 실행하지 않는 날이다 — 주말 · 공휴일 |
    | `NO_ROW_ON_EXECUTION_DAY` | 실행일인데 이력이 없다 |
    | `ROW_ON_OFF_DAY` | 실행하지 않는 날인데 이력이 있다 — 사고가 아니다 |
    | `UNKNOWN_CALENDAR` | 달력이 그날을 덮지 않아 판정할 수 없다 |

    판정은 `end_code` 와 다른 축이다. `end_code` 는 "그 판단이 어떻게 끝났나" 이고,
    판정은 "그날이 어떻게 됐나" 다.

    `start` · `end` 는 필수다. 범위가 없으면 빈 날을 계산할 수 없어
    `NO_ROW_ON_EXECUTION_DAY` 를 판정할 수 없다. 공휴일 달력을 붙여 판정하므로 설 · 추석이
    실행일로 잘못 읽히지 않는다.

    주의: 물어본 범위가 그 걷기가 실제로 진행한 범위보다 넓으면, 진행하지 않은 날도
    `NO_ROW_ON_EXECUTION_DAY` 로 나온다.

    | 상태 | 언제 |
    |---|---|
    | 200 | 이력이 있었다 · 이력이 하나도 없었다 — 둘 다 결과다 |
    | 400 | `sim_run_id` 가 비었거나 `end` 가 `start` 보다 앞이다 |

    이력이 0건이어도 404 가 아니다. 404 로 내면 "진행하지 않았다" 와 "그런 걷기가
    없다" 가 구분되지 않는다.

    제약: `sim_run_id` 가 NULL 인 이력(손으로 부른 실행이나 실행 축 없이 남은 실험 기록)은
    어느 성적표에도 나오지 않는다. 어느 걷기의 것인지 표가 알 수 없기 때문이다.
    """
    try:
        return build_walk_report(
            sim_run_id=sim_run_id,
            start=start,
            end=end,
            calendar=get_calendar(),
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
