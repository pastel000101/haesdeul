"""재무 일마감의 판정 · 계산 — 실행축 · 시작 상태 · 직전 상태 · 채권 대조 · 칸 나누기.

순서는 `service/closing.py`, SQL 은 `repository/closing.py`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.finance.domain.expenses import effective_paid_date
from app.finance.domain.tools import effective_cash_date
from app.finance.domain.values import decimal_value
from app.finance.schemas.data_port import FinanceDataNotReady
from app.finance.schemas.expenses import OPERATING_EXPENSE_CATEGORIES, PAYROLL_INTEREST_CATEGORIES

_ZERO = Decimal(0)


#: `payroll_interest_cash_out_krw` 에 들어가는 원장의 실제 비용 분류.
#:
#: 원장이 쓰는 이자 이름은 `INTEREST` 가 아니라 `LOAN_INTEREST` 다(실측
#: `expenses.expense_category`). 목록에 없으면 이자 지급이 있는 날은 마감이 통째로
#: `daily_closing_expense_category` 로 막힌다 — 2025-12-31 이 실제로 그 날이다. 이미 적힌
#: 그날 마감값(13,035,596.88 = 급여 + 대출이자)이 두 비용을 함께 세는 것이 정본 계약임을
#: 보여 준다.
#:
#: `INTEREST` 도 남긴다. 원장 이름이 바뀐 것이 아니라 모르는 이름을 하나 더 아는 것이고, 아는
#: 이름을 지우면 예전 데이터가 다시 막힌다.
#:
#: 목록 밖은 `FinanceDataNotReady` 다. 모르는 분류를 조용히 어느 칸에 넣으면 그 순간 마감이
#: 틀린 값을 확정한다 — 막히는 편이 낫다.
#: 이름의 주인은 `app.finance.schemas.expenses` 다. 여기서 다시 적으면 새 분류가 생긴 날
#: 쓰기는 받아 주는데 마감만 막히는, 원인 찾기 어려운 상태가 된다.
_PAYROLL_INTEREST_CATEGORIES: frozenset[str] = PAYROLL_INTEREST_CATEGORIES

#: `operating_expense_cash_out_krw` 에 들어가는 분류. 매입대금도 물류비도 급여·이자도
#: 아닌 잔여 운영비다.
#:
#: 이 칸이 없으면 원장이 받아 적을 수 있는 비용(예: 임차료)을 마감이 «모르는 분류» 로 거절해
#: 그날 마감이 통째로 막힌다 — 갈 곳이 없기 때문이다.
_OPERATING_EXPENSE_CATEGORIES: frozenset[str] = OPERATING_EXPENSE_CATEGORIES


@dataclass(frozen=True)
class BaselineRef:
    """새 실행이 어느 재무 상태 한 행에서 시작을 물려받았는가.

    정본 포인터는 `finance_state_id` 다. `finance_states` 의 PK 라 정확히 한 행을 가리킨다.

    `state_type` 이나 날짜로 다시 찾지 않는다. `DAY30` 은 그 행을 고른 이유이지 조회 키가
    아니다 — 실측으로 `DAY30` 행은 `FIN-DAY30-BASE` · `FIN-DAY30-LOAN` 둘이라 그것만으로는 한
    행이 정해지지 않는다. "가장 최근 상태" 도 안 된다: `SIM-BURNIN-202512` 안에는 2026-09-12
    까지의 Walk 산물이 섞여 있다.

    `from_sim_run_id` 는 계보다. 조회는 `finance_state_id` 가 하고, 이 값은 "우리가 가리킨 그
    행이 정말 그 실행의 것인가" 를 대조하는 데 쓴다.
    """

    finance_state_id: str
    from_sim_run_id: str


@dataclass(frozen=True)
class RunAxis:
    """이 마감이 서 있는 실행축. 부르는 쪽이 준 `sim_run_id` 가 정한다.

    `v_current_finance_state` 를 쓰지 않는다. 그 View 는 "지금" 을 가리키므로, 과거 실행을
    다시 닫으면 남의 실행 축 위에서 닫게 된다. 마감은 자기에게 건네진 실행의 축을 닫아야 한다.

    `sim_run_id` 문자열을 해석하지도, 축 이름을 상수로 박지도 않는다 — `sim_runs.financing_mode`
    가 그 실행의 정본이다.
    """

    financing_mode: str
    period_start: date
    #: 선언된 baseline. 선언이 없으면 `None` 이고, 선언이 깨져 있으면 여기까지
    #: 오지 않는다 (`run_axis` 가 세운다).
    baseline: BaselineRef | None = None


@dataclass(frozen=True)
class ClosingState:
    financing_mode: str
    current_cash_krw: Decimal
    receivables_krw: Decimal
    current_debt_krw: Decimal

    @property
    def cash_without_debt_krw(self) -> Decimal:
        """현재 현금에서 아직 남아 있는 대출 원금 효과를 뺀 값.

        반사실 시뮬레이션이 아니다. "대출이 없었다면 있었을 현금" 이 아니라 "지금 현금에서
        남은 원금만큼을 뺀 값" 이다. 진짜 A/B 비교는 별도 `sim_run` 을 하나 더 걸어서 한다
        (마스터 결정 ㄷ).

        누적 대출 실행액이 아니라 잔액을 뺀다. 누적 실행액을 빼면 원금을 갚아도 그만큼이
        영원히 빠진 채로 남아, 상환할수록 이 값이 낮아진다. 갚은 돈은 이미 `current_cash_krw`
        에서 나갔으므로 두 번 빼는 것이 된다.

        음수는 자료 미준비가 아니라 사실이다. 남은 원금이 보유 현금보다 크면 이 값은 음수이고,
        그것은 "대출을 빼고 보면 이만큼 모자란다" 는 재무 사실이다. 막으면 가장 위험한 날의
        마감이 통째로 사라진다 — 위험을 기록하지 않는 것과 위험이 없는 것은 다르다.

        여기서 "현금은 0 이상" 정책을 새로 만들지 않는다. 원장 값 자체의 음수 방어는
        `_daily_closing_amount` 가 따로 들고 있다.
        """
        return self.current_cash_krw - self.current_debt_krw


@dataclass(frozen=True)
class ClosingFacts:
    day_no: int
    purchase_cash_out_krw: Decimal
    logistics_cash_out_krw: Decimal
    payroll_interest_cash_out_krw: Decimal
    operating_expense_cash_out_krw: Decimal
    sales_recognized_krw: Decimal
    collection_cash_in_krw: Decimal
    base_cash_balance_krw: Decimal
    loan_execution_krw: Decimal
    loan_cash_balance_krw: Decimal
    receivables_balance_krw: Decimal
    inventory_qty_kg: Decimal
    accounting_inventory_cost_krw: Decimal

    @property
    def base_net_cash_krw(self) -> Decimal:
        """그날 순현금. 주인은 여기 하나다 — 화면도 마스터도 다시 세지 않는다."""
        return (
            self.collection_cash_in_krw
            - self.purchase_cash_out_krw
            - self.logistics_cash_out_krw
            - self.payroll_interest_cash_out_krw
            - self.operating_expense_cash_out_krw
        )


# ---------------------------------------------------------------------------
# 판정 — 읽은 행을 마감 값으로 옮긴다. DB 를 부르지 않는다.
# ---------------------------------------------------------------------------


def run_axis(rows: list, *, as_of: date) -> RunAxis:
    """이 실행의 기간과 조달 축을 한 행에서 읽는다.

    기간 검사와 축 조회가 같은 행에서 나온다 — `sim_runs` 한 행이 "이 실행은 언제부터
    언제까지, 어느 축 위에서 도는가" 를 통째로 소유하기 때문이다.
    """
    if len(rows) != 1:
        raise FinanceDataNotReady("sim_run")
    row = rows[0]
    period_start = _row_value(row, "period_start", 0)
    period_end = _row_value(row, "period_end", 1)
    financing_mode = _row_value(row, "financing_mode", 2)
    if not isinstance(period_start, date) or not isinstance(period_end, date):
        raise FinanceDataNotReady("sim_run")
    if not isinstance(financing_mode, str) or not financing_mode.strip():
        raise FinanceDataNotReady("sim_run_financing_mode")
    if not period_start <= as_of <= period_end:
        raise FinanceDataNotReady("sim_run_date_out_of_range")
    return RunAxis(
        financing_mode=financing_mode.strip(),
        period_start=period_start,
        baseline=baseline_ref(_row_value(row, "config_json", 3)),
    )


def baseline_ref(config_json: object) -> BaselineRef | None:
    """`config_json.baseline` 이 가리키는 시작 상태. 선언이 없으면 `None`.

    ```text
    {}                                          선언 없음        → None
    {"baseline": {"finance_state_id": "...",    선언 있음        → BaselineRef
                  "from_sim_run_id": "..."}}
    {"baseline": {}}                            선언이 깨졌다    → NOT_READY
    {"baseline": {"finance_state_id": ""}}      선언이 깨졌다    → NOT_READY
    ```

    선언이 깨진 것을 '선언 없음' 으로 읽지 않는다. 그러면 시작 상태를 물려받아야 할 실행이
    조용히 0 에서 시작하고, 물려받은 부채가 첫날 신규 차입으로 기록된다.

    주의: `config_json.financing_baseline` 과 다른 칸이다. 저쪽은 대출 조건(이율·기간·거치)이고
    여기는 "어느 상태 행에서 이어받았나" 다. 이름이 닮았다고 섞으면 대출 정책이 시작 상태 자리에
    들어온다.
    """
    if not isinstance(config_json, Mapping):
        return None
    if "baseline" not in config_json:
        return None
    section = config_json["baseline"]
    if not isinstance(section, Mapping):
        raise FinanceDataNotReady("baseline_finance_state_invalid")
    finance_state_id = section.get("finance_state_id")
    from_sim_run_id = section.get("from_sim_run_id")
    if not isinstance(finance_state_id, str) or not finance_state_id.strip():
        raise FinanceDataNotReady("baseline_finance_state_invalid")
    if not isinstance(from_sim_run_id, str) or not from_sim_run_id.strip():
        # 계보가 없으면 가리킨 행이 정말 그 실행의 것인지 대조할 길이 없다.
        raise FinanceDataNotReady("baseline_finance_state_invalid")
    return BaselineRef(
        finance_state_id=finance_state_id.strip(),
        from_sim_run_id=from_sim_run_id.strip(),
    )


def opening_ar_carry_of(baseline_state: ClosingState | None) -> Decimal:
    """이 실행이 출발점에서 물려받은 채권 잔액 (Opening AR Carry).

    재무 확정 기준 ① — 물려받은 `finance_states.receivables_krw` 를 그 실행의 Opening AR
    Carry 로 유지한다. 채권 행을 이관하지도, 지어내지도 않는다.

    ```text
    baseline 선언이 있다   → 그 행의 receivables_krw
    baseline 선언이 없다   → 0
    ```

    선언이 없으면 0 이고, 그때 대조식은 `receivables` 합 하나와 상태를 맞춰 보는 모양이 된다.
    번인(`SIM-BURNIN-202512`)은 `config_json.baseline` 이 없어 이 갈래로 간다 — 이미 통과해
    있는 30행이 흔들리면 안 된다.

    주의: 이 값은 개별 수금 가능한 채권이 아니다(재무 확정 기준 ⑥). 근거가 되는 채권별 12/31
    잔액이 남아 있지 않으므로 수금 대상 행으로 풀지 않고, 실행 내내 한 덩어리로 남는다. 그래서
    이 실행에서 도는 수금은 전부 그 Walk 에서 발행된 채권에서만 나온다.
    """
    if baseline_state is None:
        return _ZERO
    return baseline_state.receivables_krw


def baseline_closing_state(rows: list, baseline: BaselineRef) -> ClosingState:
    """물려받은 시작 상태 한 행. `finance_state_id` 로만 찾는다.

    계보를 함께 대조한다 — 가리킨 행이 선언한 실행의 것이 아니면 닫지 않는다. 포인터가 남의
    실행을 가리키는 날, 그 사고는 에러 없이 숫자만 바꾼다.
    """
    if len(rows) != 1:
        raise FinanceDataNotReady("baseline_finance_state")
    row = rows[0]
    if str(_row_value(row, "sim_run_id", 0)) != baseline.from_sim_run_id:
        raise FinanceDataNotReady("baseline_finance_state_invalid")
    return ClosingState(
        financing_mode=str(_row_value(row, "financing_mode", 1)),
        current_cash_krw=_daily_closing_amount(_row_value(row, "current_cash_krw", 2)),
        receivables_krw=_daily_closing_amount(_row_value(row, "receivables_krw", 3)),
        current_debt_krw=_daily_closing_amount(_row_value(row, "current_debt_krw", 4)),
    )


def exact_closing_state(rows: list) -> ClosingState:
    """마감일 그날의 실행축 상태 한 행.

    없으면 닫지 않고, 같은 날 같은 축에 두 행이면 고르지 않는다. 둘 다 값을 지어내지 않기 위한
    것이다.
    """
    if len(rows) > 1:
        raise FinanceDataNotReady("finance_state_ambiguous")
    if not rows:
        raise FinanceDataNotReady("finance_state")
    row = rows[0]
    return ClosingState(
        financing_mode=str(_row_value(row, "financing_mode", 0)),
        current_cash_krw=_daily_closing_amount(_row_value(row, "current_cash_krw", 1)),
        receivables_krw=_daily_closing_amount(_row_value(row, "receivables_krw", 2)),
        current_debt_krw=_daily_closing_amount(_row_value(row, "current_debt_krw", 3)),
    )


def prior_closing_state(rows: list) -> ClosingState | None:
    """같은 실행 · 같은 축에서 마감일 앞 가장 늦은 상태. 없으면 `None`."""
    if not rows:
        return None
    # 행이 둘이라는 것은 모호하다는 뜻이 아니다. `len(rows) > 1` 은 "이 축에 이전 상태가
    # 둘 이상 있다" 이고 그것은 일별 상태가 쌓인 정상 실행의 모습이다 — 그 조건으로 세우면
    # 실측 축(`LOAN_BASELINE`, 252행)에서 셋째 날부터 모든 마감이 `finance_state_ambiguous`
    # 로 막힌다.
    #
    # 모호한 것은 가장 늦은 날짜가 둘일 때뿐이다 — 그때만 어느 행이 직전 상태인지 고를 수
    # 없다. `readmodel/finance_state.py` 의 `load_finance_state_row` 도 같은 규율이다.
    latest_date = _row_value(rows[0], "state_date", 0)
    if len(rows) > 1 and _row_value(rows[1], "state_date", 0) == latest_date:
        raise FinanceDataNotReady("finance_state_ambiguous")
    row = rows[0]
    return ClosingState(
        financing_mode=str(_row_value(row, "financing_mode", 1)),
        current_cash_krw=_daily_closing_amount(_row_value(row, "current_cash_krw", 2)),
        receivables_krw=_daily_closing_amount(_row_value(row, "receivables_krw", 3)),
        current_debt_krw=_daily_closing_amount(_row_value(row, "current_debt_krw", 4)),
    )


def payable_recognition(row: object, *, as_of: date) -> tuple[str, Decimal, date] | None:
    """기일이 온 채무 한 행을 이번 마감에 실을지. 실으면 (채무 id, 금액, 기일)."""
    due_date = _row_value(row, "due_date", 1)
    if not isinstance(due_date, date):
        raise FinanceDataNotReady("payable_due_date")
    # 기일이 주말이면 현금은 다음 월요일에 나간다. 그 날이 아직 안 왔으면 이번 마감이 실을
    # 것이 아니다.
    if effective_cash_date(due_date) > as_of:
        return None
    payable_id = _row_value(row, "payable_id", 0)
    if not isinstance(payable_id, str) or not payable_id.strip():
        raise FinanceDataNotReady("payable_id")
    return (
        payable_id,
        _daily_closing_amount(_row_value(row, "outstanding_amount_krw", 2)),
        due_date,
    )


def recognized_total(rows: list) -> Decimal:
    """이 실행에서 이 날로 적힌 귀속의 합.

    방금 적은 것만 세지 않는다. 재마감에서 새로 적히는 것이 없어도 이 합은 그대로라,
    `daily_closings` 의 값이 두 번째 마감에 0 으로 덮이지 않는다.
    """
    total = _ZERO
    for row in rows:
        total += _daily_closing_amount(_row_value(row, "recognized_amount_krw", 0))
    return total


def split_expense_cash_out(rows: list, *, as_of: date) -> tuple[Decimal, Decimal, Decimal]:
    """그날 지급된 비용 행을 물류비 · 급여·이자 · 일반 운영비 세 칸으로 가른다."""
    logistics = _ZERO
    payroll_interest = _ZERO
    operating = _ZERO
    for row in rows:
        category = _row_value(row, "expense_category", 0)
        delivery_id = _row_value(row, "related_delivery_id", 1)
        amount = _daily_closing_amount(_row_value(row, "amount_krw", 2))
        status = _row_value(row, "status", 3)
        paid_on = effective_paid_date(
            status=str(status),
            paid_date=_row_value(row, "paid_date", 4),  # type: ignore[arg-type]
            expense_date=_row_value(row, "expense_date", 5),  # type: ignore[arg-type]
        )
        if paid_on != as_of:
            # SQL 이 이미 걸렀지만, 같은 규칙을 파이썬에서도 한 번 더 세운다 — 두 자리가 다른
            # 날짜를 «지급일» 이라고 부르기 시작하면 아무도 못 찾는다.
            continue
        if delivery_id is not None:
            logistics += amount
        elif category in _PAYROLL_INTEREST_CATEGORIES:
            payroll_interest += amount
        elif category in _OPERATING_EXPENSE_CATEGORIES:
            operating += amount
        else:
            raise FinanceDataNotReady("daily_closing_expense_category")
    return logistics, payroll_interest, operating


def ledger_amount(row: object) -> Decimal:
    """합계 조회 한 행의 금액. 행이 없으면 원장이 준비되지 않은 것이다."""
    if row is None:
        raise FinanceDataNotReady("daily_closing_ledger")
    return _daily_closing_amount(_row_value(row, "amount", 0))


def collection_delta(
    *, prior_receivables: Decimal, issued_receivables: Decimal, current_receivables: Decimal
) -> Decimal:
    collected = prior_receivables + issued_receivables - current_receivables
    if collected < 0:
        raise FinanceDataNotReady("collection_balance_mismatch")
    return collected


def loan_execution(current: ClosingState, prior: ClosingState | None) -> Decimal:
    """당일 신규 차입액(flow). 잔액(stock)이 아니다.

    원금 상환은 음수 차입이 아니다 — 그날 새로 빌린 돈이 없을 뿐이라 0 이다. 상환은
    `current_cash_krw` 와 `current_debt_krw` 가 이미 말하고 있다.

    이 값을 누적해서 `base_cash_balance_krw` 를 만들지 않는다. 그쪽은 남은 원금 잔액
    (`current_debt_krw`)을 쓴다 — `ClosingState.cash_without_debt_krw` 참조.
    """
    prior_debt = prior.current_debt_krw if prior is not None else _ZERO
    return max(current.current_debt_krw - prior_debt, _ZERO)


def closing_params(*, as_of: date, sim_run_id: str, facts: ClosingFacts) -> dict[str, object]:
    """마감 한 행에 적을 값."""
    return {
        "sim_run_id": sim_run_id,
        "close_date": as_of,
        "day_no": facts.day_no,
        "purchase_cash_out_krw": facts.purchase_cash_out_krw,
        "logistics_cash_out_krw": facts.logistics_cash_out_krw,
        "payroll_interest_cash_out_krw": facts.payroll_interest_cash_out_krw,
        "operating_expense_cash_out_krw": facts.operating_expense_cash_out_krw,
        "sales_recognized_krw": facts.sales_recognized_krw,
        "collection_cash_in_krw": facts.collection_cash_in_krw,
        "base_net_cash_krw": facts.base_net_cash_krw,
        "base_cash_balance_krw": facts.base_cash_balance_krw,
        "loan_execution_krw": facts.loan_execution_krw,
        "loan_cash_balance_krw": facts.loan_cash_balance_krw,
        "receivables_balance_krw": facts.receivables_balance_krw,
        "inventory_qty_kg": facts.inventory_qty_kg,
        "accounting_inventory_cost_krw": facts.accounting_inventory_cost_krw,
    }


def _row_value(row: object, name: str, index: int) -> object:
    if isinstance(row, dict):
        return row[name]
    return row[index]  # type: ignore[index]


def _daily_closing_amount(value: object) -> Decimal:
    try:
        money = decimal_value(value)
    except Exception as exc:  # pragma: no cover - exact exception depends on DB adapter.
        raise FinanceDataNotReady("daily_closing_ledger") from exc
    if not money.is_finite() or money < 0:
        raise FinanceDataNotReady("daily_closing_ledger")
    return money
