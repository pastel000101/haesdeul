"""마스터 이력 조회 응답 모델 — 실행 이력 · 보고서 · 번인 구간."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.master.schemas.decision import DecisionOut


class ReportOut(BaseModel):
    """`GET /master/runs/{request_id}/report` — 들고 나갈 수 있는 매입안 문서.

    문서는 서버가 Markdown 으로 만들고 화면은 그대로 내려받는다. 화면이 문서를 따로
    조립하면 화면과 문서가 서로 다른 숫자를 말하게 된다.
    """

    request_id: str
    filename: str
    #: Markdown 전문. 붙여 넣기·메신저·이슈 어디에도 그대로 들어간다.
    markdown: str


class DailyClosingOut(BaseModel):
    """하루치 마감 한 줄. 번인 구간의 실제 마감 값이며 에이전트가 만든 값이 아니다."""

    close_date: date
    day_no: int
    #: 무차입 기준 현금. 재무가 답하는 `available_cash` 와 다르다 —
    #: 그쪽은 대출 실행분이 더해진 값이다. 둘을 같은 줄에 두면 화면이 거짓말을 한다.
    base_cash_balance_krw: float | None = None
    loan_cash_balance_krw: float | None = None
    receivables_balance_krw: float | None = None
    inventory_qty_kg: float | None = None
    sales_recognized_krw: float | None = None
    collection_cash_in_krw: float | None = None
    purchase_cash_out_krw: float | None = None
    #: 마감되지 않은 날이 섞이면 그 사실이 답의 일부다 — 지우지 않는다.
    closed: bool = False


class BurnInOut(BaseModel):
    """`GET /master/burn-in` — 에이전트가 판단을 시작하기 전 번인 구간의 실행 정보와 일별 마감.

    에이전트의 첫 판단(예: "살 안이 없다")만 보면 시스템이 고장 난 것처럼 읽힐 수 있다.
    그 앞 구간에 회사가 어떻게 왔는지를 결론 옆에 보여 주려는 조회다.

    읽기 전용이다. DB 에 있는 값을 모양만 바꿔 돌려주고, 합계나 증감률을 계산하지 않으며,
    하루를 진행시키지 않는다.
    """

    sim_run_id: str
    run_type: str
    period_start: date
    period_end: date
    #: 에이전트가 처음 판단하는 날. 번인의 마지막 날과 같다.
    as_of: date
    status: str
    financing_mode: str | None = None
    note: str | None = None
    closings: list[DailyClosingOut] = []


class RunHistoryOut(BaseModel):
    """`GET /master/runs/{request_id}` — 그 요청이 어떻게 됐나.

    같은 업무 키에 실행이 여러 행이면 가장 최근 매입 실행 하나를 돌려준다. 결정은 그
    업무 키의 것을 전부 싣는다.

    `plan` 은 응답 원문(`response_payload`) 안이 아니라 이력 행의 별도 컬럼에서 온다.
    검증 Tool 의 실행 계획 온전성 검사가 그 컬럼을 읽는다. `plan_signature` 는 `plan`
    에서 (agent, mode, call_seq) 로 만든다.
    """

    request_id: str
    #: 돌려주는 이 행의 id. 같은 업무 키에 실행이 여러 행이라, 이게 없으면 화면이
    #: "지금 보는 계획이 어느 실행 것인가" 와 "이 결정이 그 실행을 가리키나" 를 대조할
    #: 수 없다.
    run_id: str | None = None
    as_of: date
    agent: str
    cycle: str
    runtime_status: str
    elapsed_ms: int | None = None
    created_at: datetime

    plan: list[dict[str, Any]] = []
    plan_signature: list[tuple[str, str, int]] = []

    request_payload: dict[str, Any] = {}
    response_payload: dict[str, Any] = {}

    decisions: list[DecisionOut] = Field(
        default=[],
        description=(
            "사람의 결정 이력. 최신 하나가 `is_current` 다. 비어 있으면 결정이 없다는 뜻이다 — "
            '"그 요청 어떻게 됐냐"에 한 번의 호출로 답하기 위해 여기 싣는다.'
        ),
    )
