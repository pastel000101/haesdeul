"""재무 일마감 — 마스터 마감 등록소가 넘긴 연결로 인식 → 지급 → 사실 읽기 → 마감 행.

마스터 경로는 연결을 넘기고 commit 은 마스터가 한다. 연결 없이 부르는 대체 경로
(`close_finance_day(conn=None)` — 앱 안에서 그렇게 부르는 곳은 없다)만 스스로 빌려 한
트랜잭션으로 닫는다.

판정은 `domain/closing.py`, SQL 은 `repository/closing.py`. 이름은 마스터 `close_day` 와 겹치지
않게 `close_finance_day` 다(설계서 §진입점 2).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.core import db as core_db
from app.finance.domain.closing import (
    ClosingFacts,
    ClosingState,
    RunAxis,
    baseline_closing_state,
    closing_params,
    collection_delta,
    exact_closing_state,
    ledger_amount,
    loan_execution,
    opening_ar_carry_of,
    payable_recognition,
    prior_closing_state,
    recognized_total,
    run_axis,
    split_expense_cash_out,
)
from app.finance.repository.closing import (
    insert_daily_closing,
    insert_payable_recognition,
    select_baseline_state,
    select_exact_state,
    select_paid_expenses,
    select_prior_states,
    select_receivables_issued,
    select_receivables_outstanding,
    select_recognized_amounts,
    select_run_axis,
    select_sales_recognized,
    select_unrecognized_due_payables,
    update_daily_closing,
)
from app.finance.schemas.closing import FinanceDayClosingResult
from app.finance.schemas.data_port import FinanceDataNotReady
from app.finance.service.inventory import load_inventory_snapshot_as_of
from app.finance.service.settlement import settle_recognized_payables

_ZERO = Decimal(0)


def close_finance_day(
    *, as_of: date, sim_run_id: str, conn: Any | None = None
) -> FinanceDayClosingResult:
    """Close a Finance day using the supplied transaction when one exists.

    마스터 마감(`FinanceClosingAdapter`)은 자기 연결을 넘긴다 — 그 경로는 여기서 연결을
    빌리지도 commit 하지도 않는다. 연결 없이 부르면(앱 안에서 그렇게 부르는 곳은 없다) 풀에서
    하나 빌려 한 트랜잭션으로 닫는다.
    """

    if conn is not None:
        return close_finance_day_on(conn, as_of=as_of, sim_run_id=sim_run_id)
    with core_db.connection() as owned_connection, core_db.transaction(owned_connection):
        return close_finance_day_on(owned_connection, as_of=as_of, sim_run_id=sim_run_id)


def close_finance_day_on(conn: Any, *, as_of: date, sim_run_id: str) -> FinanceDayClosingResult:
    """받은 연결로 그날 재무 마감 한 행을 적는다. commit 하지 않는다."""
    if not isinstance(sim_run_id, str) or not sim_run_id.strip():
        raise ValueError("sim_run_id must be a non-blank string")

    facts = load_closing_facts(conn, as_of=as_of, sim_run_id=sim_run_id)
    created = write_daily_closing(
        conn, closing_params(as_of=as_of, sim_run_id=sim_run_id, facts=facts)
    )
    return FinanceDayClosingResult(
        part="finance",
        status="CLOSED",
        closed=[f"{sim_run_id}:{as_of.isoformat()}"],
        created=created,
    )


def load_closing_facts(conn: Any, *, as_of: date, sim_run_id: str) -> ClosingFacts:
    """이 하루의 마감 사실. 실행축에서 실제로 일어난 것만 읽는다.

    마스터 결정 ㄷ (확정) — 마감은 실행축의 사실만 기록하고, 무차입/차입 A/B 비교는 `sim_run`
    을 하나 더 실제로 걸어서 한다. 그래서 여기서 축을 둘 읽지 않는다.

    같은 날짜의 `BASE_NO_LOAN` 을 따로 요구하지 않는다. 하루 넘김(`FinanceDayOpening`)은
    실행축 하나만 전진시키므로 그 행은 생기지 않고, 요구하면 정상적으로 연 하루가
    `base_finance_state` 로 막힌다.

    채권 대조는 출발분과 실행 중 발생분을 나눠서 한다 (재무 확정 기준 ④).

      ```text
      Opening AR Carry  +  그 Walk 에서 발행되어 남아 있는 receivables
                        ==  finance_states.receivables_krw
      ```

    왼쪽을 `receivables` 합 하나로 두면 안 된다. 시작을 물려받은 실행은 그 표에 행이 0행인데
    상태에는 물려받은 잔액이 들어 있어(실측 21,922,555원 대 0원), 걷기 206일이 전부
    `receivables_balance_mismatch` 로 막힌다.

    대조를 없앤 것이 아니다. 왼쪽을 나눴을 뿐이고, 어긋나면 막는다(재무 확정 기준 ⑤).
    """
    axis = run_axis(select_run_axis(conn, sim_run_id=sim_run_id), as_of=as_of)

    # 순서: 인식 → 지급 → 사실 읽기 (#637).
    #
    #    ① 오늘 곡선에 실을 채무를 인식한다 (#615 · 여기서 상태를 건드리지 않는다)
    #    ② 인식된 것을 실제로 지급한다 — 현금과 미지급 채무가 줄어든다
    #    ③ 그 뒤에 상태를 읽어 마감 사실을 만든다
    #
    # ③이 ②보다 먼저면 마감이 지급 전 현금을 기말잔액으로 적는다. 그러면 그 날의 `Δ잔액` 과
    # `Σ순현금` 이 지급액만큼 어긋난 채로 장부에 남는다 — 실측(`SIM-CHAIN-V9`)에서 55일이 그
    # 상태였다.
    recognize_due_payables(conn, sim_run_id=sim_run_id, as_of=as_of)
    settle_recognized_payables(conn, sim_run_id=sim_run_id, as_of=as_of)

    state = exact_closing_state(
        select_exact_state(
            conn, sim_run_id=sim_run_id, financing_mode=axis.financing_mode, as_of=as_of
        )
    )
    baseline_state = (
        None
        if axis.baseline is None
        else baseline_closing_state(
            select_baseline_state(conn, finance_state_id=axis.baseline.finance_state_id),
            axis.baseline,
        )
    )
    prior = prior_state(
        conn,
        sim_run_id=sim_run_id,
        axis=axis,
        as_of=as_of,
        baseline_state=baseline_state,
    )

    issued_receivables = ledger_amount(
        select_receivables_issued(conn, sim_run_id=sim_run_id, as_of=as_of)
    )
    collection_cash_in = collection_delta(
        prior_receivables=prior.receivables_krw if prior is not None else _ZERO,
        issued_receivables=issued_receivables,
        current_receivables=state.receivables_krw,
    )
    opening_ar_carry = opening_ar_carry_of(baseline_state)
    receivables_balance = opening_ar_carry + ledger_amount(
        select_receivables_outstanding(conn, sim_run_id=sim_run_id, as_of=as_of)
    )
    if receivables_balance != state.receivables_krw:
        raise FinanceDataNotReady("receivables_balance_mismatch")

    inventory = load_inventory_snapshot_as_of(conn, sim_run_id=sim_run_id, as_of=as_of)
    purchase_cash_out = purchase_cash_out_on(conn, sim_run_id=sim_run_id, as_of=as_of)
    logistics_cash_out, payroll_interest_cash_out, operating_expense_cash_out = (
        expense_cash_out(conn, sim_run_id=sim_run_id, as_of=as_of)
    )
    return ClosingFacts(
        day_no=(as_of - axis.period_start).days + 1,
        purchase_cash_out_krw=purchase_cash_out,
        logistics_cash_out_krw=logistics_cash_out,
        payroll_interest_cash_out_krw=payroll_interest_cash_out,
        operating_expense_cash_out_krw=operating_expense_cash_out,
        sales_recognized_krw=ledger_amount(
            select_sales_recognized(conn, sim_run_id=sim_run_id, as_of=as_of)
        ),
        collection_cash_in_krw=collection_cash_in,
        # 대출 제외 곡선 — 실행축 현금에서 남은 원금을 뺀 값.
        base_cash_balance_krw=state.cash_without_debt_krw,
        # 당일 신규 차입 flow. stock(잔액)이 아니다.
        loan_execution_krw=loan_execution(state, prior),
        # 대출 포함 곡선 — 실행축 현금 그대로.
        loan_cash_balance_krw=state.current_cash_krw,
        receivables_balance_krw=receivables_balance,
        inventory_qty_kg=inventory.quantity_kg,
        accounting_inventory_cost_krw=inventory.inventory_book_value_krw,
    )


def prior_state(
    conn: Any,
    *,
    sim_run_id: str,
    axis: RunAxis,
    as_of: date,
    baseline_state: ClosingState | None,
) -> ClosingState | None:
    """이 하루의 직전 상태. 세 갈래를 이 순서로 고른다.

    ```text
    1. 같은 실행에 이전 상태가 있다        → 그것이 직전이다
    2. 없고, baseline 이 선언돼 있다        → 물려받은 시작 상태가 직전이다
    3. 없고, baseline 선언도 없다           → 직전이 없다 (`None`)
    ```

    1번이 2번보다 먼저다. 실행이 하루라도 진행됐으면 그 실행의 어제가 직전이지 물려받은
    시작점이 아니다. 순서를 뒤집으면 둘째 날부터 계속 시작점과 비교하게 되고, 매일이 첫날처럼
    보인다.

    `전날 행이 없다` 와 `전날 값이 0이다` 는 다르다. 부채를 물려받은 새 실행에서 직전을 0 으로
    접으면 `max(D - 0, 0)` 이 되어 있지도 않은 첫날 신규 차입이 기록된다(실측 45,272,104원).
    그래서 선언된 baseline 은 반드시 풀려야 하고, 풀리지 않으면 `baseline_closing_state` 가
    세운다.

    3번을 `모든 실행은 baseline 이 있어야 한다` 로 일반화하지 않는다. 이 실행이 시작을
    물려받아야 하는지 아닌지를 가를 정본은 `config_json.baseline` 선언뿐이고, `run_type` 으로
    추측하는 규칙을 여기서 새로 만들지 않는다. 선언이 깨진 경우는 2번에서 이미 막힌다 —
    조용히 3번으로 흘러가지 않는다.

    `baseline_state` 는 호출자가 이미 읽어서 건네준 그 한 행이다. 여기서 다시 조회하지 않는다
    — 같은 사실을 두 번 읽으면 둘이 갈리는 날이 온다. Opening AR Carry 도 같은 행에서
    나오므로, 그 행의 주인은 `load_closing_facts` 하나다.
    """
    same_run = prior_closing_state(
        select_prior_states(
            conn, sim_run_id=sim_run_id, financing_mode=axis.financing_mode, as_of=as_of
        )
    )
    if same_run is not None:
        return same_run
    return baseline_state


def purchase_cash_out_on(conn: Any, *, sim_run_id: str, as_of: date) -> Decimal:
    """이 날 현금곡선에 실린 매입대금.

    ```text
    ① 기일이 왔는데 아직 안 실은 payable 을 귀속 원장에 적는다
    ② 이 실행에서 이 날로 적힌 귀속을 통째로 더한다
    ```

    `status` 로 「실었나」를 읽지 않는다. `OPEN`/`PARTIAL` 은 "아직 안 갚았다" 는 채무 상태이지
    "아직 현금곡선에 안 실었다" 가 아니다. 그래서 조건 `effective_cash_date(due_date) ==
    as_of` 를 `<= as_of` 로 여는 것으로 풀지 않는다 — 그러면 기일 지난 채무가 갚을 때까지
    날마다 다시 실린다.

    그런데 `== as_of` 로는 늦게 생긴 payable 을 영영 못 잡는다. 승인 D일에는 행이 없고,
    pending transition 이 D+1 에 적용하면서 `due_date = D` 로 만든다. 그때는 이미
    `effective_cash_date(D) != D+1` 이라 어느 마감도 집지 않는다(실측 `SIM-CHAIN-V5` 에서
    73건 27,484,900원).

    멱등: ②가 핵심이다. 같은 날을 두 번 닫으면 ①이 아무것도 안 적지만, ②가 이미 적힌 것을
    다시 더하므로 `daily_closings` 값은 그대로다. 새로 적은 것만 더하면 재마감이 그 칸을 0 으로
    덮는다.
    """
    recognize_due_payables(conn, sim_run_id=sim_run_id, as_of=as_of)
    return recognized_total(
        select_recognized_amounts(conn, sim_run_id=sim_run_id, as_of=as_of)
    )


def recognize_due_payables(conn: Any, *, sim_run_id: str, as_of: date) -> None:
    """기일이 온 payable 중 아직 안 실은 것을 귀속 원장에 적는다.

    주말 이월 규칙(`effective_cash_date`)은 파이썬 한 곳이 주인이다. SQL 로 옮겨 적으면 같은
    규칙이 두 벌이 되고, 한쪽만 고치는 날 현금이 다른 날로 간다.

    멱등: DB 가 최종 방어선이다. 「없으면 넣는다」를 애플리케이션이 판단하면 같은 날을 동시에
    두 번 닫는 경합에서 두 줄이 들어간다. `(sim_run_id, payable_id)` PK 와
    `ON CONFLICT DO NOTHING` 이 그것을 막는다.
    """
    for row in select_unrecognized_due_payables(conn, sim_run_id=sim_run_id, as_of=as_of):
        recognition = payable_recognition(row, as_of=as_of)
        if recognition is None:
            continue
        payable_id, amount, due_date = recognition
        insert_payable_recognition(
            conn,
            sim_run_id=sim_run_id,
            payable_id=payable_id,
            recognized_date=as_of,
            recognized_amount_krw=amount,
            due_date=due_date,
        )


def expense_cash_out(
    conn: Any, *, sim_run_id: str, as_of: date
) -> tuple[Decimal, Decimal, Decimal]:
    """그날 실제로 나간 운영비를 세 칸으로 가른다.

    ```text
    related_delivery_id 가 붙었다        → 물류비
    급여 · 이자 분류                     → 급여·이자
    그 밖의 아는 운영 분류               → 일반 운영비
    모르는 분류                          → 막는다
    ```

    기준일은 `paid_date` 다 — 발생일이 아니다. 9월 16일에 생긴 임차료를 20일에 내면 현금은
    20일에 빠진다. 발생일로 세면 마감이 «아직 안 나간 돈» 을 나갔다고 적는다.

    `paid_date` 가 비어 있는 기존 PAID 행에 한해 `expense_date` 를 지급 기준일로 읽는다
    (`effective_paid_date`). 읽기 전용 호환이고, 원장에 날짜를 채워 넣지 않는다 — 추측한
    날짜가 사실인 척하게 두지 않는다.

    모르는 분류에서 막는다. 갈 칸이 있다고 해서 아무 이름이나 받으면, 그 순간 마감은 틀린
    값을 «확정» 한다. 막히는 편이 낫다.
    """
    return split_expense_cash_out(
        select_paid_expenses(conn, sim_run_id=sim_run_id, as_of=as_of), as_of=as_of
    )


def write_daily_closing(conn: Any, params: dict[str, object]) -> int:
    """그날 마감 한 행을 적는다. 새로 적었으면 1, 이미 있어 고쳐 적었으면 0."""
    if insert_daily_closing(conn, params):
        return 1
    if update_daily_closing(conn, params) != 1:
        raise FinanceDataNotReady("daily_closing_write")
    return 0
