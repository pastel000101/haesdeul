"""원장 값 읽기 — Decimal 정규화 · 금액 한 칸 · dict/tuple 행 칸."""

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from app.finance.schemas.data_port import FinanceDataNotReady


def decimal_value(value: Any) -> Decimal:
    """Finance 숫자 입력을 Decimal로 정규화한다."""
    if isinstance(value, bool):
        raise TypeError("boolean values are not valid numeric inputs")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        raise TypeError("float is not an accepted business numeric input")
    return Decimal(str(value))


def money_amount(value: Any, field: str) -> Decimal:
    """원장에서 읽은 금액 한 칸. 못 읽으면 값을 지어내지 않고 막는다.

    "읽을 수 없다" 와 "0원이다" 는 다른 사실이다. 못 읽은 칸을 0 으로 놓으면 그 0 이
    마감·지급 계산에 그대로 들어가고, 그 뒤로는 아무도 그것이 실제 0 인지 못 읽은 값인지
    구분할 수 없다.

    음수도 막는다. 원장에 적힌 금액 칸은 음수가 될 수 없고, 음수가 왔다는 것은 그 행이 이미
    깨졌다는 뜻이다. 계산 결과(예: 지급 뒤 현금)가 음수인 것은 다른 이야기이고 그것은
    사실이므로 막지 않는다.
    """
    try:
        amount = decimal_value(value)
    except Exception as exc:  # pragma: no cover - adapter-dependent
        raise FinanceDataNotReady(field) from exc
    if not amount.is_finite() or amount < 0:
        raise FinanceDataNotReady(field)
    return amount


def row_value(row: Any, name: str, index: int = 0) -> Any:
    """dict row와 tuple row를 같은 방식으로 읽는다."""
    if isinstance(row, Mapping):
        return row[name]
    return row[index]
