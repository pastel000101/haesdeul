"""판매 재무 검증 Capability — **결정론적이다. LLM 이 들어오지 않는다.**

이 파일이 소유하는 것
    Sales 회신 payload → Finance 내부 모델 파싱 · 없는 사실 식별 ·
    계산(`tools`)과 판정(`rules`)의 조립 · 자기 완결적 내부 결과

여기 **없는 것**
    산술 공식 · 판정 임계값 · Master AgentReply · Planner/Finalizer 호출
    → `tools` · `rules` · `adapter` 소유다.

★ **없는 것을 채우지 않는다.** 판매 마진 정책 · 최대 결제일수 · 여신한도 ·
  회수위험 임계값은 현재 저장소에 권위 있는 값이 없다. 그때 이 계층이 하는 일은
  성공한 척이 아니라 **무엇이 없어서 못 했는지 이름을 대는 것**이다.

★ 두 가지 "못 했다"를 섞지 않는다.

  ```text
  INPUT_INCOMPLETE    제안에 사실이 빠졌다 (Sales/Master 쪽) — Finance 는 멀쩡하다
  RUNTIME_NOT_READY   Finance 가 가진 정책/데이터가 없다 (Finance 쪽)
  ```

  섞으면 영업이 못 고치는 것을 고치라는 말이 되고, 재무가 고쳐야 할 것이 영업
  탓으로 넘어간다.

★ 2026-09-29 재구성 BL-014: `finance/capabilities/sales.py` 에서 DataPort 로 읽는 Tool 실행만
  옮겼다. 판단 ·
  계산은 `domain/sales_validation.py`, 모델은 `schemas/sales_validation.py`.
"""

from datetime import timedelta
from decimal import Decimal
from typing import Any

from app.finance.domain.sales_policy import load_finance_sales_mvp_policy
from app.finance.domain.sales_validation import (
    build_sales_validation_payload,
    evaluate_sales_scenario,
    parse_sales_validation_input,
)
from app.finance.domain.tools import (
    build_proposed_sales_collection_event,
    calculate_collection_date,
    calculate_sales_amount,
    project_sales_scenario_cashflow,
    summarize_partner_receivables,
)
from app.finance.schemas.data_port import FinanceAsOfDataPort
from app.finance.schemas.sales_validation import (
    PartnerReceivableFacts,
    SalesScenarioCashflow,
    SalesValidationInput,
)
from app.finance.service.capabilities.procurement import load_context

# ---------------------------------------------------------------------------
# Harness 진입점 — 인자를 받지 않는다
# ---------------------------------------------------------------------------


def run_sales_validation(
    data_port: FinanceAsOfDataPort, args: dict[str, Any], state: Any
) -> dict[str, Any]:
    """SALES_VALIDATION Tool 본체. **Planner 가 준 값을 쓰지 않는다.**

    ★ `args` 는 비어 있어야 하고 실제로 버린다. 제안 숫자는 request payload 가,
      정책은 Finance Policy 가 소유한다 — 모델이 수량·단가·원가·결제일수·여신을
      만들거나 베껴 넣을 자리를 두지 않는다.

    ★ **정책과 사실을 가른다.**
        정책 — 마진 임계값 · 최대 결제일수 · 회수위험 판정 방식.
               Finance/Sales MVP Policy v0.1 이 소유하고 실행마다 같다.
        사실 — 여신한도 · 거래처 채권. 거래처/계약이 소유하는 권위 있는 값이라
               재무가 기본값을 만들 수 없다. 아직 조회 계약이 없어 ``None`` 이고,
               그때 판정은 값을 지어내는 대신 RUNTIME_NOT_READY 로 닫힌다.
    """
    del args
    payload = state.request.payload
    sales_input, _ = parse_sales_validation_input(payload)
    policy = load_finance_sales_mvp_policy()

    minimum_cash: Decimal | None = None
    scenario_cashflow: SalesScenarioCashflow | None = None
    receivable_facts: PartnerReceivableFacts | None = None
    credit_limit: Decimal | None = None
    if sales_input is not None:
        minimum_cash, scenario_cashflow = _load_sales_cashflow_context(
            data_port, state, sales_input
        )
        receivable_facts = _load_partner_receivable_facts(data_port, state, sales_input)
        credit_limit = data_port.load_partner_credit_limit(
            state.request.context.as_of, sales_input.partner_id
        )

    return build_sales_validation_payload(
        evaluate_sales_scenario(
            payload,
            finance_minimum_margin_rate=policy.finance_minimum_margin_rate,
            finance_warning_margin_rate=policy.finance_warning_margin_rate,
            max_finance_allowed_payment_terms_days=(policy.max_finance_allowed_payment_terms_days),
            collection_risk_mode=policy.collection_risk_mode,
            # 🔴 여신한도는 정책이 아니라 **거래처가 소유한 사실**이다. 이제
            #    `partner_credit_limits` 가 그 정본이고, 재무는 **읽기만** 한다 —
            #    여기 기본값을 두면 없는 한도를 재무가 발명하게 된다.
            #
            # ★ 행이 없으면 `None` 이고, 그때 여신 판정은 닫힌다. `0` 은 **한도 0원
            #   이라는 사실**이라 판정한다 — 둘을 같은 값으로 만들지 않는다.
            credit_limit_krw=credit_limit,
            # 채권은 실 원장에 있다. 그래서 이쪽만 실제 사실로 채운다 —
            # 여신이 안 열렸다고 회수위험까지 눈을 감을 이유는 없다.
            receivable_facts=receivable_facts,
            # 여기 둘은 실재하는 Finance 자료다.
            minimum_cash_balance_krw=minimum_cash,
            scenario_cashflow=scenario_cashflow,
        )
    )


def _load_partner_receivable_facts(
    data_port: FinanceAsOfDataPort,
    state: Any,
    sales_input: SalesValidationInput,
) -> PartnerReceivableFacts:
    """거래처 채권 사실을 실 원장에서 만든다. **없으면 없는 채로 세운다.**

    ★ 빈 목록은 **채권이 0원이라는 사실**이다 — 신규 거래처를 자료 미비로 다루지
      않는다. 반대로 조회 자체가 실패하면 `load_partner_receivables` 가
      `FinanceDataNotReady` 로 세우고, 그 실행은 `RUNTIME_NOT_READY` 가 된다.
      "못 읽었다" 와 "0원이다" 가 같은 결과를 내면 안 된다.

    ★ 기준일은 이 실행의 `as_of` 하나다. 연체 판정(`due_date < as_of`)도 같은 날을
      쓴다 — 조회 기준일과 판정 기준일이 갈리면 어제 기준으로 읽은 채권을 오늘
      기준으로 연체 판정하는 일이 생긴다.
    """
    as_of = state.request.context.as_of
    return summarize_partner_receivables(
        partner_id=sales_input.partner_id,
        as_of=as_of,
        receivables=data_port.load_partner_receivables(as_of, sales_input.partner_id),
    )


def _load_sales_cashflow_context(
    data_port: FinanceAsOfDataPort,
    state: Any,
    sales_input: SalesValidationInput,
) -> tuple[Decimal | None, SalesScenarioCashflow | None]:
    """**재무 최소현금 정책**과, 확정 현금 Event 위에 제안 회수를 얹은 SCENARIO 투영.

    ```text
    최소현금 정책   실행마다 있는 재무 자료      제안과 무관하게 항상 읽는다
    SCENARIO 투영   제안의 회수일이 있어야 선다  없으면 만들지 않는다
    ```

    ★ 둘은 없는 이유가 다르므로 한 `return` 에 묶지 않는다. 묶으면 제안에 날짜가
      빠진 것이 *"최소현금 정책이 없다"* 로 보고된다.
    """
    ctx = state.request.context
    # 🔴 **정책 조회를 날짜에 묶지 않는다.** 예전에는 회수 기준일이 없으면 여기서
    #    곧장 `(None, None)` 으로 돌아섰고, 그래서 **판매 제안에 날짜가 빠진 것만으로
    #    최소현금 정책까지 "없는 값"** 이 됐다. 정책은 실행마다 있는 재무 자료이고
    #    제안에 무엇이 빠졌는지와 무관하다 — 둘을 한 `return` 에 묶으면 없는 이유가
    #    서로를 가린다.
    #
    # ★ **정책만 읽는다.** `load_context` 는 급여·의무·채권까지 함께 읽고 급여 출처가
    #   없으면 세운다 — 투영을 만들 때는 필요한 준비이지만, 최소현금 한 값을 읽으려고
    #   그 문턱을 넘게 하면 **투영이 필요 없는 실행이 급여 출처 때문에 막힌다.**
    minimum_cash = data_port.load_policy(ctx.as_of, ctx.policy_version).minimum_cash_balance_krw

    if sales_input.collection_reference_date is None or sales_input.payment_days is None:
        # 회수일을 못 구하면 투영만 만들지 않는다. 날짜를 지어내면 그 순간 없는
        # 사실이 현금흐름에 들어간다 — 정책은 그대로 돌려준다.
        return minimum_cash, None

    position, policy, base_events = load_context(data_port, state)
    horizon = ctx.as_of + timedelta(days=policy.cashflow_projection_days)
    sales_amount = calculate_sales_amount(
        quantity_kg=sales_input.quantity_kg, unit_price_krw=sales_input.unit_price_krw
    )
    proposed = build_proposed_sales_collection_event(
        proposal_ref=sales_input.scenario_id,
        collection_date=calculate_collection_date(
            reference_date=sales_input.collection_reference_date,
            payment_days=sales_input.payment_days,
        ),
        sales_amount_krw=sales_amount,
        source_ref=sales_input.source_ref,
    )
    scenario_cashflow = project_sales_scenario_cashflow(
        as_of=state.request.context.as_of,
        current_cash_krw=Decimal(position["current_cash_krw"]),
        horizon_end=horizon,
        base_cash_events=base_events,
        proposed_collection=proposed,
    )
    return minimum_cash, scenario_cashflow
