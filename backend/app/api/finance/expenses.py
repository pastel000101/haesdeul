"""운영비 — `GET /finance/expense-categories` · `POST /finance/expenses[/{id}/settle|cancel]`.

HTTP 만: 요청 → `finance/service/expenses.py` → 응답, 업무 거절 → 상태 코드. 마스터 ask 도 같은
service 를 부른다.
"""

from fastapi import APIRouter, status

from app.api.finance.deps import DbConnection, rejected
from app.finance.schemas.expenses import (
    KNOWN_EXPENSE_CATEGORIES,
    ExpenseCancel,
    ExpenseCreate,
    ExpenseSettle,
)
from app.finance.schemas.write_rejection import FinanceWriteRejected
from app.finance.service import expenses as expense_service

router = APIRouter(prefix="/finance", tags=["finance"])


@router.get("/expense-categories")
def list_expense_categories() -> dict[str, object]:
    """새 비용에 쓸 수 있는 분류 목록. 화면은 분류를 따로 적어 두지 않고 이 목록을 쓴다."""
    return {"categories": sorted(KNOWN_EXPENSE_CATEGORIES)}


@router.post("/expenses", status_code=status.HTTP_201_CREATED)
def create_operating_expense(expense: ExpenseCreate, conn: DbConnection) -> dict[str, object]:
    """운영비 한 건을 `ACCRUED` 로 적는다."""
    try:
        return expense_service.accrue_operating_expense(conn, expense)
    except FinanceWriteRejected as error:
        raise rejected(error) from error


@router.post("/expenses/{expense_id}/settle")
def settle_operating_expense(
    expense_id: str, request: ExpenseSettle, conn: DbConnection
) -> dict[str, object]:
    """`ACCRUED` 비용을 지급하고 같은 거래에서 현금을 줄인다."""
    try:
        return expense_service.pay_accrued_expense(conn, expense_id, request)
    except FinanceWriteRejected as error:
        raise rejected(error) from error


@router.post("/expenses/{expense_id}/cancel")
def cancel_operating_expense(
    expense_id: str, request: ExpenseCancel, conn: DbConnection
) -> dict[str, object]:
    """`ACCRUED` 비용을 취소한다. 이미 지급된 비용은 여기로 오지 못한다."""
    try:
        return expense_service.cancel_accrued_expense(conn, expense_id, request)
    except FinanceWriteRejected as error:
        raise rejected(error) from error
