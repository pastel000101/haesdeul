"""재무 상태 행 고르기 — 축 하나 · 가장 늦은 날 하나 · 그날 정확히 한 행 · 음수 부채 거절.

SQL 은 `repository/finance_states.py`.
"""

from collections.abc import Mapping
from decimal import Decimal

from app.finance.schemas.data_port import FinanceDataNotReady
from app.finance.schemas.finance_state import FinanceRuntimeAxis


def runtime_axis_from_rows(rows: list[dict[str, object]]) -> FinanceRuntimeAxis:
    """축 행이 정확히 하나일 때만 축이다."""
    if not rows:
        # 없으면 없는 것이다. 다른 실행의 축으로 대신하지 않는다.
        raise LookupError("Current Finance State was not found")
    if len(rows) > 1:
        raise FinanceDataNotReady("finance_runtime_axis_ambiguous")
    row = rows[0]
    return FinanceRuntimeAxis(
        sim_run_id=str(row["sim_run_id"]), financing_mode=str(row["financing_mode"])
    )


def state_row_as_of(rows: list[dict[str, object]]) -> dict[str, object]:
    """가장 늦은 상태 한 건. 없거나, 가장 늦은 날이 둘이면 막는다."""
    if not rows:
        raise FinanceDataNotReady("historical_finance_position")
    if len(rows) == 2 and rows[0]["state_date"] == rows[1]["state_date"]:
        raise FinanceDataNotReady("finance_state_ambiguous")
    row = rows[0]
    reject_negative_debt(row)
    return row


def current_view_row(rows: list[dict[str, object]]) -> dict[str, object]:
    """View 의 현재 행. 없거나 실행이 여럿이면 고르지 않는다."""
    if not rows:
        raise LookupError("Current Finance State was not found")
    if len(rows) > 1:
        raise FinanceDataNotReady("finance_runtime_axis_ambiguous")
    row = rows[0]
    reject_negative_debt(row)
    return row


def reject_negative_debt(row: Mapping[str, object]) -> None:
    """부채는 음수일 수 없다. 원천 행에서 한 번 막는다.

    여기서 막지 않으면 음수 부채가 "빚 없음"으로 읽힌다. 부채 축의 판단은
    `current_debt_krw > 0` 인지로 갈리는데, 음수는 그 분기에서 0 과 같은 쪽에 떨어진다 —
    잘못된 DB 상태가 "확인할 부채가 없다" 는 정상 응답으로 둔갑하고, 부채 정책 검증도 상환
    일정도 통째로 건너뛴다.

    원천 행에 두는 이유: 이 검사를 부르는 행 고르기(`current_state_row_on`)가 두 런타임 경로의
    유일한 공통 입구다.

        current_state_row_on
          ├─ finance_snapshot_on → get_current_finance_runtime_context
          └─ PostgresFinanceAsOfDataPort.load_finance_position → load_debt_schedule

    `FinanceSnapshot` 검증만 믿으면 AsOf DataPort 는 원시 dict 를 그대로 쓰므로 그 경로로
    음수가 빠져나간다. 스키마 제약은 이중 방어이지 대체가 아니다.

    못 믿을 상태이지 프로그램 오류가 아니므로 `RUNTIME_NOT_READY` 로 접힌다.
    """
    debt = row.get("current_debt_krw")
    if debt is not None and Decimal(str(debt)) < 0:
        raise FinanceDataNotReady("finance_state_debt_invalid")


def exact_state_row(rows: list) -> dict:
    """그날 재무 상태는 정확히 한 행이어야 한다. 없거나 둘 이상이면 막는다.

    자금 조정과 운영비 지급이 같은 검사로 쓴다.
    """
    if not rows:
        raise FinanceDataNotReady("historical_finance_position")
    if len(rows) != 1:
        raise FinanceDataNotReady("finance_state_ambiguous")
    return rows[0]
