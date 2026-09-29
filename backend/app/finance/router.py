"""재무·자금 Agent API 라우터.

★ 2026-09-29 재구성 BL-014: 라우터에는 **HTTP 만** 남았다 — 요청 모델 → service · readmodel →
  응답, 업무 예외 → 상태 코드. 재무 쓰기 6종의 SQL · 판정 · 트랜잭션은 `service/`
  (`credit_limits` · `collections` · `cash_adjustments` · `expenses`)로 갔고, 마스터 ask 가 같은
  service 를 부른다(전에는 ask 가 이 파일의 핸들러를 파이썬 함수로 불렀다). 요청 모델은
  `schemas/` 에 있다. 핸들러 이름 · docstring(OpenAPI 설명) · URL · 상태 코드 · 문구는 그대로다.
  이 라우트들을 `api/finance/` 로 옮기는 것은 BL-019 다.
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.contracts.envelope import AgentReply, AgentRequest
from app.core import db as core_db
from app.finance.adapter import finance_port
from app.finance.domain import messages
from app.finance.readmodel.credit_limits import credit_limit_history
from app.finance.readmodel.runs import get_finance_execution, get_finance_run, list_finance_runs
from app.finance.schemas.agent import FinalVerdict, FinanceCycle, RuntimeStatus
from app.finance.schemas.cash_adjustments import CashAdjustmentChange
from app.finance.schemas.collections import ReceivableCollectionChange
from app.finance.schemas.credit_limits import CreditLimitChange, CreditLimitHistoryItem
from app.finance.schemas.expenses import (
    KNOWN_EXPENSE_CATEGORIES,
    ExpenseCancel,
    ExpenseCreate,
    ExpenseSettle,
)
from app.finance.schemas.runs import FinanceAgentRunResponse
from app.finance.schemas.write_rejection import FinanceWriteRejected
from app.finance.service import cash_adjustments as cash_adjustment_service
from app.finance.service import collections as collection_service
from app.finance.service import credit_limits as credit_limit_service
from app.finance.service import expenses as expense_service

router = APIRouter(prefix="/finance", tags=["finance"])

#: 요청 처리 동안 공통 풀에서 빌린 연결 (2026-09-29 풀 전환). **commit 하지 않는다** —
#: 쓰기 트랜잭션은 service 가 `core_db.transaction(conn)` 으로 연다(종전 핸들러의 연결 블록과
#: 같은 경계: 정상 commit · 예외 rollback). `scope="function"` 이라 응답을 보내기 전에 연결을
#: 돌려준다. 🔴 `/agent` 처럼 LLM 을 기다리는 라우트에는 걸지 않는다.
DbConnection = Annotated[core_db.Connection, Depends(core_db.db_connection, scope="function")]

#: 재무 쓰기 service 가 받지 않은 요청 → 상태 코드 (종전 핸들러와 같은 값).
_REJECTION_STATUS = {
    "NOT_FOUND": status.HTTP_404_NOT_FOUND,
    "CONFLICT": status.HTTP_409_CONFLICT,
    "INVALID": 422,
}


def _rejected(error: FinanceWriteRejected) -> HTTPException:
    return HTTPException(status_code=_REJECTION_STATUS[error.reason], detail=error.message)


@router.get("/credit-limits", response_model=list[CreditLimitHistoryItem])
def get_credit_limits(
    partner_id: Annotated[str, Query(min_length=1)],
    as_of: date,
    conn: DbConnection,
) -> list[CreditLimitHistoryItem]:
    """한 거래처의 여신한도 이력을 최신 적용일부터 반환한다."""
    try:
        return credit_limit_history(conn, partner_id=partner_id, as_of=as_of)
    except LookupError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error


@router.post("/credit-limits", status_code=status.HTTP_201_CREATED)
def register_credit_limit(change: CreditLimitChange, conn: DbConnection) -> dict[str, object]:
    """열린 한도 기간을 끝내고 새 기간을 추가한다; 과거 금액은 덮어쓰지 않는다."""
    try:
        return credit_limit_service.change_credit_limit(conn, change)
    except FinanceWriteRejected as error:
        raise _rejected(error) from error


@router.get("/expense-categories")
def list_expense_categories() -> dict[str, object]:
    """새 비용에 쓸 수 있는 분류. **화면이 이 목록을 손으로 다시 적지 않는다.**"""
    return {"categories": sorted(KNOWN_EXPENSE_CATEGORIES)}


@router.post("/expenses", status_code=status.HTTP_201_CREATED)
def create_operating_expense(expense: ExpenseCreate, conn: DbConnection) -> dict[str, object]:
    """운영비 한 건을 `ACCRUED` 로 적는다."""
    try:
        return expense_service.accrue_operating_expense(conn, expense)
    except FinanceWriteRejected as error:
        raise _rejected(error) from error


@router.post("/expenses/{expense_id}/settle")
def settle_operating_expense(
    expense_id: str, request: ExpenseSettle, conn: DbConnection
) -> dict[str, object]:
    """`ACCRUED` 비용을 지급하고 같은 거래에서 현금을 줄인다."""
    try:
        return expense_service.pay_accrued_expense(conn, expense_id, request)
    except FinanceWriteRejected as error:
        raise _rejected(error) from error


@router.post("/expenses/{expense_id}/cancel")
def cancel_operating_expense(
    expense_id: str, request: ExpenseCancel, conn: DbConnection
) -> dict[str, object]:
    """`ACCRUED` 비용을 취소한다. 이미 지급된 비용은 여기로 오지 못한다."""
    try:
        return expense_service.cancel_accrued_expense(conn, expense_id, request)
    except FinanceWriteRejected as error:
        raise _rejected(error) from error


@router.post("/receivables/collections", status_code=status.HTTP_201_CREATED)
def record_receivable_collection(
    change: ReceivableCollectionChange, conn: DbConnection
) -> dict[str, object]:
    """한 채권의 실제 전액/부분 수금을 누적 전이로 기록한다."""
    try:
        return collection_service.record_collection(conn, change)
    except FinanceWriteRejected as error:
        raise _rejected(error) from error


@router.post("/cash-adjustments", status_code=status.HTTP_201_CREATED)
def create_cash_adjustment(change: CashAdjustmentChange, conn: DbConnection) -> dict[str, object]:
    """사용자 자금 입금·출금을 근거와 함께 기록한다."""
    try:
        return cash_adjustment_service.apply_cash_adjustment(conn, change)
    except FinanceWriteRejected as error:
        raise _rejected(error) from error


@router.post("/agent", summary="Finance v2.2 Tool-Using Agent")
def run_finance_agent(request: AgentRequest) -> AgentReply:
    """Master와 동일한 Finance Port를 통해 Agent를 실행한다."""
    reply, _metadata = finance_port(request)
    return reply


@router.get("/agent/runs/{run_id}", summary="Finance v2.2 execution metadata")
def get_finance_execution_by_id(run_id: UUID) -> dict[str, object]:
    try:
        return get_finance_execution(run_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=messages.RUN_NOT_FOUND) from error


@router.get(
    "/runs",
    response_model=list[FinanceAgentRunResponse],
    summary="Finance Agent 실행이력 목록 조회",
)
def get_finance_runs(
    cycle: FinanceCycle | None = None,
    as_of: date | None = None,
    runtime_status: RuntimeStatus | None = None,
    verdict: FinalVerdict | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[FinanceAgentRunResponse]:
    """cycle, as_of, runtime_status 필터로 최근 실행이력을 반환한다."""
    return list_finance_runs(
        cycle=cycle,
        as_of=as_of,
        runtime_status=runtime_status,
        verdict=verdict,
        limit=limit,
    )


@router.get(
    "/runs/{run_id}",
    response_model=FinanceAgentRunResponse,
    summary="Finance Agent 실행이력 단건 조회",
)
def get_finance_run_by_id(run_id: UUID) -> FinanceAgentRunResponse:
    """run_id에 해당하는 실행이력을 반환한다."""
    try:
        return get_finance_run(run_id)
    except LookupError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=messages.RUN_NOT_FOUND,
        ) from error
