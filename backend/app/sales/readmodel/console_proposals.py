"""그날 만든 판매안 read model — 매입 화면의 «금일 매입안» 과 같은 자리다.

판매 화면에서 «오늘 무엇을 팔자고 했나» 를 보여 준다. 확정된 판매와 채권은 이미 끝난
일이고, 매입 화면처럼 그날의 안을 카드로 펴 놓고 근거와 걸리는 것을 함께 보여 주는
것이 이 read model 이다.

판매가 자기 저장소를 읽는다. 안 자체는 `sales_agent_runs` 의
`response_payload.payload.scenarios` 에 있다. 다시 만들지 않고 저장된 것을 편다.

재무 판정을 판매가 계산하지 않는다. 통과 여부는 재무가 자기 이력에 남긴 값이고,
여기서는 같은 요청 키와 안 번호로 찾아 읽기만 한다. 판매가 마진이나 여신으로 판정을
흉내 내면 두 화면이 다른 답을 말하는 날이 온다.

주의: 실행 축이 컬럼에 없다. `sales_agent_runs` 는 `sim_run_id` 칸을 갖고 있지 않아
요청 봉투 안의 값으로 거른다 (`console_runs` 와 같은 방식).

SQL 은 `repository/console_proposals.py`, 안 하나와 그날 화면의 자리를 가르는 판정은
`domain/console_proposals.py`, 응답 모델은 `schemas/console_proposals.py` 다.
"""

from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core import db as core_db
from app.sales.domain.console_proposals import overall_state, presentation_state
from app.sales.repository.console_proposals import load_proposal_rows, load_sale_statuses
from app.sales.schemas.console_proposals import (
    ConsoleSalesProposal,
    ConsoleSalesProposalsResponse,
    ConsoleSalesStrategy,
)


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        # 못 읽는 값을 0 으로 바꾸지 않는다. 0 원 제안과 «못 읽었다» 는 다른 사실이다.
        return None


def _int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value))
    except ValueError:
        return None


def _date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _texts(value: object) -> list[str]:
    """문장 목록. 문자열이 아닌 항목은 버리지 않고 글자로 적는다."""
    if not isinstance(value, list):
        return []
    return [item if isinstance(item, str) else str(item) for item in value]


def _sale_status(
    found: dict[tuple[str, str], str], request_id: str, scenario_id: str
) -> str | None:
    """`sale_id_for`(`domain/sale_ledger.py`)가 만든 번호는 `…-{안 번호}` 로 끝난다.

    다른 안과 섞이지 않게 끝까지 맞춘다.
    """
    suffix = f"-{scenario_id}"
    for (request, sale_id), status in found.items():
        if request == request_id and sale_id.endswith(suffix):
            return status
    return None


def _strategy(payload: dict[str, Any]) -> ConsoleSalesStrategy | None:
    """저장된 전략 라벨. 아무 칸도 없으면 `None` 이다 — 빈 칸을 지어내지 않는다.

    여기 담기는 것은 저장된 어휘뿐이다. 모델이 돌려준 원문이나 HTTP 응답 본문은
    들어오지 않는다.
    """
    keys = (
        "strategy_source",
        "strategy_llm_status",
        "strategy_llm_failure_reason",
        "strategy_clamped_reason_codes",
        "strategy_collapsed",
        "strategy_collapse_reason_codes",
    )
    if not any(key in payload for key in keys):
        return None
    return ConsoleSalesStrategy(
        source=_text(payload.get("strategy_source")),
        llm_status=_text(payload.get("strategy_llm_status")),
        llm_failure_reason=_text(payload.get("strategy_llm_failure_reason")),
        clamped_reason_codes=_texts(payload.get("strategy_clamped_reason_codes")),
        collapsed=bool(payload.get("strategy_collapsed")),
        collapse_reason_codes=_texts(payload.get("strategy_collapse_reason_codes")),
    )


def _recommendation_reason(payload: dict[str, Any], recommended: bool) -> str | None:
    """추천한 안에만, 판매가 저장한 이유를 싣는다. 비어 있으면 `None` 이다."""
    if not recommended:
        return None
    for key in ("recommendation", "llm"):
        block = payload.get(key)
        if isinstance(block, dict):
            reason = block.get("recommendation_reason")
            if isinstance(reason, str) and reason.strip():
                return reason.strip()
    return None


def get_console_sales_proposals(*, sim_run_id: str, as_of: date) -> ConsoleSalesProposalsResponse:
    """그날의 판매안. 안이 없으면 빈 목록이지, 0원 제안이 아니다."""
    with core_db.read_connection() as conn:
        rows: list[ConsoleSalesProposal] = []
        requests: set[str] = set()
        hidden = 0
        raw_rows = load_proposal_rows(conn, sim_run_id=sim_run_id, as_of=as_of)
        sale_statuses = load_sale_statuses(
            conn,
            sim_run_id=sim_run_id,
            request_ids=sorted({str(raw["request_id"]) for raw in raw_rows}),
        )
        for raw in raw_rows:
            request_id = str(raw["request_id"])
            requests.add(request_id)
            scenario = raw["scenario"]
            if not isinstance(scenario, dict):
                #  그날 돌았지만 안을 못 만든 요청이다 (입력 미비 등). 요청 수에는 남는다.
                continue
            quantity = _decimal(scenario.get("quantity_kg"))
            if quantity is not None and quantity <= 0:
                # 팔 물량이 0이면 안이 선 것이 아니다. 재무도 검토할 것이 없어
                # 판정이 안 붙고, 화면에서는 «검토 전» 으로 남아 밀린 안처럼 보인다.
                hidden += 1
                continue
            payload = raw["payload"] if isinstance(raw["payload"], dict) else {}
            recommended_id = payload.get("recommended_scenario_id")
            scenario_id = scenario.get("scenario_id")
            is_recommended = recommended_id is not None and str(recommended_id) == str(scenario_id)
            summary = raw["financial_summary"] if isinstance(raw["financial_summary"], dict) else {}
            sales_status = _text(scenario.get("status"))
            finance_verdict = _text(raw.get("finance_verdict"))
            missing = _texts(payload.get("missing_capabilities"))
            state, unresolved_reasons = presentation_state(
                sales_status=sales_status,
                finance_verdict=finance_verdict,
                missing_capabilities=missing,
            )
            cost_basis = scenario.get("inventory_cost_basis")
            cost_basis = cost_basis if isinstance(cost_basis, dict) else {}
            supply = scenario.get("supply")
            supply = supply if isinstance(supply, dict) else {}
            rows.append(
                ConsoleSalesProposal(
                    request_id=request_id,
                    history_run_id=_text(raw.get("history_run_id")),
                    scenario_id="" if scenario_id is None else str(scenario_id),
                    scenario_type=_text(scenario.get("scenario_type")),
                    objective=_text(scenario.get("objective")),
                    item=_text(scenario.get("item")),
                    partner_id=_text(scenario.get("partner_id")),
                    quantity_kg=quantity,
                    unit_price_krw=_decimal(scenario.get("unit_price_krw")),
                    reported_sales_amount_krw=_decimal(scenario.get("reported_sales_amount_krw")),
                    payment_days=_int(scenario.get("payment_days")),
                    delivery_date=_date(scenario.get("delivery_date")),
                    status=sales_status,
                    rationale=_texts(scenario.get("rationale")),
                    risks=_texts(scenario.get("risks")),
                    uncertainties=_texts(scenario.get("uncertainties")),
                    finance_verdict=finance_verdict,
                    finance_status=_text(raw.get("finance_status")),
                    finance_reason_codes=_failing_reasons(raw.get("rule_results")),
                    contribution_margin_krw=_decimal(summary.get("contribution_margin_krw")),
                    contribution_margin_rate=_decimal(summary.get("contribution_margin_rate")),
                    current_partner_ar_krw=_decimal(summary.get("current_partner_ar_krw")),
                    available_credit_krw=_decimal(summary.get("available_credit_krw")),
                    projected_partner_ar_krw=_decimal(summary.get("projected_partner_ar_krw")),
                    credit_limit_krw=_decimal(summary.get("credit_limit_krw")),
                    required_collection_before_sale_krw=_decimal(
                        summary.get("required_collection_before_sale_krw")
                    ),
                    credit_utilization_rate=_decimal(summary.get("credit_utilization_rate")),
                    expected_credit_recovery_date=_date(summary.get("expected_credit_recovery_date")),
                    missing_capabilities=missing,
                    evidence_refs=_texts(scenario.get("evidence_refs")),
                    source_ref=_text(scenario.get("source_ref")),
                    cost_basis_amount_krw=_decimal(cost_basis.get("amount_krw")),
                    cost_basis_quantity_kg=_decimal(cost_basis.get("quantity_kg")),
                    cost_basis_method=_text(cost_basis.get("cost_method")),
                    cost_basis_refs=_texts(cost_basis.get("source_refs")),
                    confirmed_quantity_kg=_decimal(supply.get("confirmed_quantity_kg")),
                    conditional_quantity_kg=_decimal(supply.get("conditional_quantity_kg")),
                    additional_supply_required=_bool(supply.get("additional_supply_required")),
                    ml_support_used=_bool(scenario.get("ml_support_used")),
                    recommended=is_recommended,
                    recommendation_reason=_recommendation_reason(payload, is_recommended),
                    sale_status=_sale_status(sale_statuses, request_id, str(scenario_id)),
                    presentation_state=state,  # type: ignore[arg-type]
                    # 통과한 안만 확정 경계를 넘을 수 있다. «확인 필요» 도 막는다 —
                    # 사람이 봐야 한다는 말은 아직 승인이 아니라는 뜻이다.
                    approval_blocked=state != "PRESENTABLE",
                    unresolved_reason_codes=unresolved_reasons,
                    strategy=_strategy(payload),
                )
            )
        counted = Counter(row.presentation_state for row in rows)
        return ConsoleSalesProposalsResponse(
            sim_run_id=sim_run_id,
            as_of=as_of,
            request_count=len(requests),
            hidden_zero_quantity=hidden,
            state=overall_state(counted, has_rows=bool(rows)),
            presentable_count=counted["PRESENTABLE"],
            unresolved_count=counted["UNRESOLVED"],
            rejected_count=counted["REJECTED"],
            review_required_count=counted["REVIEW_REQUIRED"],
            rows=rows,
        )


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _bool(value: object) -> bool | None:
    """`None` 은 «모른다» 다. `False` 로 바꾸면 «확인했고 아니다» 가 된다."""
    return None if value is None else bool(value)


def _failing_reasons(rule_results: object) -> list[str]:
    """판정을 가른 규칙의 사유만 모은다.

    최상위 `reason_codes` 를 쓰지 않는다. 그 배열에는 통과 사유까지 함께 들어
    있어(실측: PASS 판정에도 일곱 개), 그대로 쓰면 통과한 규칙이 거절 사유로 읽힌다.

    `REVIEW_REQUIRED` 도 함께 담는다 — 왜 «확인 필요» 인지도 사유가 있어야 한다.
    """
    if not isinstance(rule_results, list):
        return []
    collected: list[str] = []
    for rule in rule_results:
        if not isinstance(rule, dict):
            continue
        if rule.get("verdict") not in {"FAIL", "REVIEW_REQUIRED"}:
            continue
        for code in rule.get("reason_codes") or ():
            text = str(code)
            if text not in collected:
                collected.append(text)
    return collected
