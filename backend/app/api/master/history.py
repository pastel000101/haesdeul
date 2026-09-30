"""실행 이력 · 보고서 조회 — `GET /master/runs/{request_id}[/report]` · `/master/burn-in` ·
`/master/walks/{sim_run_id}/report`.

HTTP 만: 조회는 `master/readmodel/history.py`, 걷기 성적표는 `master/report/walk_report.py`(공휴일
달력은 `master/readmodel/holiday_calendar.py`).

★ 2026-09-30 재구성 BL-019: `app/master/router.py` 에서 옮겼다 — 핸들러 이름 · docstring(OpenAPI
  설명) · URL · 상태 코드 · 문구 그대로.
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
    """업무 키(`REQ-20260827-0001`)로 실행 계획과 요청·응답 원문을 돌려준다.

    ★ **검증 Tool 의 ④ 실행 계획 온전성 검사(M-16)가 읽는 경로**이기도 하다 (§3.7.4).
      `plan` 은 응답 원문 안이 아니라 별도 컬럼에서 오므로, 응답 스키마가 바뀌어도
      검증이 따라 흔들리지 않는다.
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
    """안마다 분할·조달·지급 일정과 근거를 편 문서.

    ★ **못 한 것을 같이 싣는다.** 지적·확인 필요·못 돈 검사·입력 출처가 안 옆에
      있어야 들고 나간 사람이 그 숫자를 어떻게 읽어야 하는지 안다.
      **결론만 담은 문서가 가장 위험하다.**
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

    🔴 **결론 옆에 경로를 두기 위한 것이다.** 에이전트가 12-31 에 *"살 안이 없다"*
      고 답하는데 그 앞 30일을 안 보면 시스템이 고장 난 것처럼 읽힌다.

    ★ 읽기 전용이다 — 하루를 진행시키는 것은 승인이 발주로 흘러가야 성립하고,
      그건 각 파트의 상태 전이 로직이다 (아직 없다 · 별도 이슈).
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
    summary="걷기 성적표 — 안 돈 날을 처음으로 셀 수 있다",
)
def master_walk_report(sim_run_id: str, start: date, end: date) -> WalkReport:
    """한 걷기가 범위 안에서 어떻게 갔나. **날마다 한 줄이고 빈 날도 한 줄이다.**

    🔴 **「행이 없다」를 두 가지로 가른다.** 표는 없는 것을 말할 수 없어서, 범위를
       받아 빈 날을 계산하고 실행일 여부로 그 뜻을 가른다.

    | 판정 | 뜻 |
    |---|---|
    | `WALKED` | 🟢 돌았다 |
    | `SKIPPED_OFF_DAY` | 🟢 안 도는 날이다 — 주말·공휴일 |
    | `NO_ROW_ON_EXECUTION_DAY` | 🔴 **실행일인데 행이 없다** |
    | `ROW_ON_OFF_DAY` | 🟡 안 도는 날인데 행이 있다 — 사고가 아니다 |
    | `UNKNOWN_CALENDAR` | ⚠️ 달력이 그 날을 안 덮어 모른다 |

    ★ **판정을 `end_code` 와 섞지 않았다.** `end_code` 는 *"그 판단이 어떻게
      끝났나"* 이고 판정은 *"그 날이 어떻게 됐나"* 다 — 축이 다르다.

    🔴 **`start` · `end` 가 필수다.** 기본값으로 "전체" 를 만들지 않는다 — 범위가
       없으면 빈 날을 계산할 수 없고, 그러면 `NO_ROW_ON_EXECUTION_DAY` 가 성립하지
       않는다.

    ★ **공휴일 달력을 여기서 붙인다.** 안 붙이면 설·추석이 실행일로 읽혀
      `NO_ROW_ON_EXECUTION_DAY` 가 되고, **없는 공백**이 화면에 뜬다.

    | 상태 | 언제 |
    |---|---|
    | 200 | 걷기가 있었다 · **행이 하나도 없었다** — 둘 다 사실이다 |
    | 400 | `end` 가 `start` 보다 앞이다 |

    🔴 **행이 0건이어도 404 가 아니다.** 404 로 내면 *"안 걸었다"* 와 *"그런 걷기가
       없다"* 가 같아진다 — 이 성적표가 가르려는 것이 정확히 그런 종류의 접힘이다.

    ⚠️ **한계 — 축이 NULL 인 행은 어느 성적표에도 안 나온다.** 실측(2026-09-09)으로
      이 표 1,322행 중 1,206행이 그것이고, 손으로 부른 것과 옛 실험이다. 감추는
      것이 아니라 **어느 걷기 것인지 표가 모른다.**
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
