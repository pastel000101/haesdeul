"""발화 도메인 행동 실행 — 조회는 부서 readmodel, 쓰기는 부서 service 를 부르고 결과를 문장으로
  옮긴다.

★ 2026-09-30 재구성 BL-018: `master/ask_service.py` 에서 옮겼다 — `DOMAIN_READ_ACTIONS`,
  `DOMAIN_WRITE_ACTIONS`, `_partner_id`, `_financing_mode`, `_find_receivable`, `domain_preview`,
  `_domain_read`, `_finance_write`, `_domain_write`, `run_domain_action`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

from app.core import db as core_db
from app.core.settings import SHOWN_SIM_RUN_ID
from app.finance.readmodel.console_credit import get_console_credit
from app.finance.readmodel.console_expenses import get_console_expenses
from app.finance.readmodel.console_payables import get_console_payables
from app.finance.readmodel.console_receivables import get_console_receivables
from app.finance.readmodel.dashboard import get_finance_cashflow, get_finance_dashboard
from app.finance.schemas.cash_adjustments import CashAdjustmentChange
from app.finance.schemas.collections import ReceivableCollectionChange
from app.finance.schemas.credit_limits import CreditLimitChange
from app.finance.schemas.expenses import ExpenseCancel, ExpenseCreate, ExpenseSettle
from app.finance.schemas.write_rejection import FinanceWriteRejected
from app.finance.service.cash_adjustments import apply_cash_adjustment
from app.finance.service.collections import record_collection
from app.finance.service.credit_limits import change_credit_limit
from app.finance.service.expenses import (
    accrue_operating_expense,
    cancel_accrued_expense,
    pay_accrued_expense,
)
from app.master.domain.ask_parsers import (
    DomainClarification,
    dump,
    integer,
    money,
    period_of,
    slots_of,
    user_date,
    won,
)
from app.master.llm.schemas import Intent
from app.master.report.chat_reports import (
    render_finance_chat_report,
    render_logistics_chat_report,
    render_sales_chat_report,
)
from app.master.schemas.ask import DomainActionAnswer
from app.master.schemas.decision import DecisionRejected
from app.master.schemas.sales import SalesRunRequest
from app.master.service.sales import run_sales
from app.sales.readmodel.console_partners import get_console_partner_detail, get_console_partners
from app.sales.readmodel.console_proposals import get_console_sales_proposals
from app.sales.schemas.partners import PartnerAlreadyExists, PartnerInputRejected, PartnerNotFound
from app.sales.service.partners import create_partner, update_partner

DOMAIN_READ_ACTIONS = frozenset(
    {
        "FINANCE_SUMMARY_GET",
        "FINANCE_CREDIT_LIMIT_GET",
        "FINANCE_EXPENSE_LIST",
        "FINANCE_CASHFLOW_GET",
        "FINANCE_RECEIVABLES_GET",
        "FINANCE_PAYABLES_GET",
        "FINANCE_REPORT_GENERATE",
        "SALES_PROPOSALS_TODAY",
        "SALES_CONFIRMED_TODAY",
        "SALES_REPORT_GENERATE",
        #: 보고서 생성은 **조회다.** 쓰기 목록에 넣지 않는다.
        "LOGISTICS_REPORT_GENERATE",
        "PARTNER_LIST",
        "PARTNER_DETAIL_GET",
    }
)
DOMAIN_WRITE_ACTIONS = frozenset(
    {
        "FINANCE_CASH_ADJUSTMENT_CREATE",
        "FINANCE_CREDIT_LIMIT_UPSERT",
        "FINANCE_COLLECTION_CREATE",
        "FINANCE_EXPENSE_CREATE",
        "FINANCE_EXPENSE_SETTLE",
        "FINANCE_EXPENSE_CANCEL",
        "SALES_PROPOSAL_CREATE",
        "PARTNER_CREATE",
        "PARTNER_UPDATE",
    }
)


def _partner_id(intent: Intent, *, as_of: date) -> str:
    slots = slots_of(intent)
    ref = (slots.partner_id or slots.partner_ref or "").strip()
    if not ref:
        raise DomainClarification("거래처를 알려주세요.")
    rows = get_console_partners(sim_run_id=SHOWN_SIM_RUN_ID, as_of=as_of, query=ref).rows
    exact = [
        row for row in rows if row.partner_id == ref or (row.partner_name or "").strip() == ref
    ]
    hits = exact or rows
    if not hits:
        raise DomainClarification(f"'{ref}' 거래처를 찾지 못했습니다.")
    if len(hits) > 1:
        choices = " · ".join(
            f"{row.partner_name or '이름 없음'}({row.partner_id})" for row in hits[:5]
        )
        raise DomainClarification(f"거래처가 여러 곳입니다: {choices}. 하나를 지정해 주세요.")
    return hits[0].partner_id


def _financing_mode(intent: Intent, *, as_of: date) -> str:
    slots = slots_of(intent)
    if slots.financing_mode:
        return slots.financing_mode
    dashboard = get_finance_dashboard(sim_run_id=SHOWN_SIM_RUN_ID, as_of=as_of)
    modes = list(dict.fromkeys(state.financing_mode for state in dashboard.states))
    if not modes:
        raise DomainClarification("해당 기준일의 재무 장부가 준비되지 않았습니다.")
    if len(modes) > 1:
        labels = {"BASE_NO_LOAN": "대출 없이 운영", "LOAN_BASELINE": "대출 반영"}
        choices = " · ".join(labels.get(mode, mode) for mode in modes)
        raise DomainClarification(f"어느 재무 장부에 기록할까요? {choices}")
    return modes[0]


def _find_receivable(intent: Intent, *, as_of: date) -> str:
    slots = slots_of(intent)
    if slots.receivable_id:
        return slots.receivable_id
    partner_id = _partner_id(intent, as_of=as_of)
    rows = get_console_receivables(
        sim_run_id=SHOWN_SIM_RUN_ID, as_of=as_of, partner_id=partner_id
    ).rows
    open_rows = [row for row in rows if row.outstanding_amount_krw > 0]
    if not open_rows:
        raise DomainClarification("해당 거래처에 남아 있는 받을 돈이 없습니다.")
    if len(open_rows) > 1:
        choices = " · ".join(
            f"{row.receivable_id}({won(row.outstanding_amount_krw)})" for row in open_rows[:5]
        )
        raise DomainClarification(f"받을 돈이 여러 건입니다: {choices}. 하나를 지정해 주세요.")
    return open_rows[0].receivable_id


def domain_preview(intent: Intent, *, as_of: date) -> str:
    action = intent.domain_action or ""
    slots = slots_of(intent)

    if action == "FINANCE_CASH_ADJUSTMENT_CREATE":
        direction = "입금" if slots.direction == "INFLOW" else "출금"
        amount = money(slots.amount, field="금액")
        mode = _financing_mode(intent, as_of=as_of)
        return (
            f"{direction}을 기록합니다.\n- 금액: {won(amount)}\n- 장부: {mode}\n"
            f"- 기준일: {as_of.isoformat()}\n- 확인 자료: {slots.source_ref}\n진행할까요?"
        )

    if action == "FINANCE_CREDIT_LIMIT_UPSERT":
        partner_id = _partner_id(intent, as_of=as_of)
        amount = money(slots.credit_limit, field="새 여신한도", allow_zero=True)
        effective = user_date(
            slots.effective_from, as_of=as_of, field="적용 시작일", required=True
        )
        credit = get_console_credit(sim_run_id=SHOWN_SIM_RUN_ID, as_of=as_of)
        current = next((row for row in credit.partners if row.partner_id == partner_id), None)
        return (
            f"{partner_id}의 여신한도를 변경합니다.\n"
            f"- 현재 한도: {won(None if current is None else current.credit_limit_krw)}\n"
            f"- 새 한도: {won(amount)}\n- 적용일: {effective}\n"
            f"- 근거 등급: {slots.evidence_grade}\n- 확인 자료: {slots.source_ref}\n진행할까요?"
        )

    if action == "FINANCE_COLLECTION_CREATE":
        receivable_id = _find_receivable(intent, as_of=as_of)
        mode = _financing_mode(intent, as_of=as_of)
        amount = "전액" if slots.collect_all else won(money(slots.amount, field="수금액"))
        return (
            f"수금을 기록합니다.\n- 받을 돈: {receivable_id}\n- 수금: {amount}\n"
            f"- 장부: {mode}\n- 확인 자료: {slots.source_ref}\n진행할까요?"
        )

    if action == "FINANCE_EXPENSE_CREATE":
        amount = money(slots.amount, field="비용 금액")
        expense_date = user_date(
            slots.expense_date, as_of=as_of, field="비용 발생일", required=True
        )
        due_date = user_date(slots.due_date, as_of=as_of, field="지급 예정일", required=True)
        return (
            f"비용을 ACCRUED로 등록합니다.\n- 분류: {slots.expense_category}\n"
            f"- 금액: {won(amount)}\n- 발생일: {expense_date}\n- 지급 예정일: {due_date}\n"
            f"- 근거: {slots.evidence_id}\n진행할까요?"
        )

    if action == "FINANCE_EXPENSE_SETTLE":
        paid = user_date(slots.paid_date, as_of=as_of, field="실제 지급일", required=True)
        mode = _financing_mode(intent, as_of=as_of)
        return (
            f"{slots.expense_id} 비용을 지급 처리합니다.\n"
            f"- 지급일: {paid}\n- 장부: {mode}\n진행할까요?"
        )

    if action == "FINANCE_EXPENSE_CANCEL":
        return f"{slots.expense_id} 비용을 취소합니다. 현금은 바꾸지 않습니다.\n진행할까요?"

    if action == "SALES_PROPOSAL_CREATE":
        partner_id = _partner_id(intent, as_of=as_of)
        qty = money(slots.requested_quantity_kg, field="판매 요청 수량")
        return (
            f"판매 후보를 생성합니다.\n- 거래처: {partner_id}\n- 품목: {intent.item}\n"
            f"- 수량: {qty}kg\n- 판매 유형: {slots.business_mode}\n진행할까요?"
        )

    if action == "PARTNER_CREATE":
        return (
            "새 거래처를 등록합니다.\n"
            f"- 코드: {slots.partner_id}\n- 이름: {slots.partner_name}\n"
            f"- 유형: {slots.partner_type}\n진행할까요?"
        )

    if action == "PARTNER_UPDATE":
        partner_id = _partner_id(intent, as_of=as_of)
        return (
            f"{partner_id} 거래처 기본정보를 수정합니다. 여신한도는 건드리지 않습니다.\n진행할까요?"
        )

    return "이 작업을 실행할까요?"


def _domain_read(
    intent: Intent, *, as_of: date, sim_run_id: str = SHOWN_SIM_RUN_ID
) -> DomainActionAnswer:
    action = intent.domain_action or ""
    slots = slots_of(intent)

    if action == "FINANCE_SUMMARY_GET":
        value = get_finance_dashboard(sim_run_id=SHOWN_SIM_RUN_ID, as_of=as_of)
        lines = ["재무 현황입니다."]
        for state in value.states:
            lines.append(
                f"- {state.financing_mode}: 현재 현금 {won(state.current_cash_krw)}"
                f" · 운영 여유 {won(state.operating_cash_buffer_krw)}"
            )
        return DomainActionAnswer(
            domain="finance", action=action, text="\n".join(lines), data=dump(value)
        )

    if action == "FINANCE_CREDIT_LIMIT_GET":
        partner_id = _partner_id(intent, as_of=as_of)
        value = get_console_credit(sim_run_id=SHOWN_SIM_RUN_ID, as_of=as_of)
        row = next((item for item in value.partners if item.partner_id == partner_id), None)
        if row is None:
            text = f"{partner_id}의 여신 기록이 없습니다."
            data = {"partner_id": partner_id, "credit": None}
        else:
            text = (
                f"{row.partner_name or row.partner_id} 여신 현황\n"
                f"- 한도: {won(row.credit_limit_krw)}\n"
                f"- 현재 미수: {won(row.current_ar_krw)}\n"
                f"- 가용 여신: {won(row.available_credit_krw)}"
            )
            data = dump(row)
        return DomainActionAnswer(domain="finance", action=action, text=text, data=data)

    if action == "FINANCE_EXPENSE_LIST":
        start, end = period_of(intent, as_of=as_of)
        value = get_console_expenses(
            sim_run_id=SHOWN_SIM_RUN_ID,
            as_of=as_of,
            from_date=start,
            to_date=end,
            category=slots.expense_category,
        )
        summary = value.summary
        text = (
            f"비용 {len(value.rows)}건\n- 미지급: {won(summary.accrued_krw)}\n"
            f"- 지급: {won(summary.paid_krw)}\n- 취소: {won(summary.cancelled_krw)}"
        )
        return DomainActionAnswer(domain="finance", action=action, text=text, data=dump(value))

    if action == "FINANCE_CASHFLOW_GET":
        start, end = period_of(intent, as_of=as_of)
        value = get_finance_cashflow(
            sim_run_id=SHOWN_SIM_RUN_ID,
            as_of=end,
            days=max(1, min(400, (end - start).days + 1)),
        )
        return DomainActionAnswer(
            domain="finance",
            action=action,
            text=f"{start} ~ {end} 자금 흐름 {len(value.cashflow)}일 기록을 불러왔습니다.",
            data=dump(value),
        )

    if action == "FINANCE_RECEIVABLES_GET":
        value = get_console_receivables(sim_run_id=SHOWN_SIM_RUN_ID, as_of=as_of)
        return DomainActionAnswer(
            domain="finance",
            action=action,
            text=f"아직 받을 돈 {won(value.summary.total_outstanding_krw)} · {len(value.rows)}건",
            data=dump(value),
        )

    if action == "FINANCE_PAYABLES_GET":
        value = get_console_payables(sim_run_id=SHOWN_SIM_RUN_ID, as_of=as_of)
        return DomainActionAnswer(
            domain="finance",
            action=action,
            text=(
                f"아직 지급할 매입대금 {won(value.summary.total_outstanding_krw)}"
                f" · 연체 {won(value.summary.overdue_krw)}"
            ),
            data=dump(value),
        )

    if action == "FINANCE_REPORT_GENERATE":
        start, end = period_of(intent, as_of=as_of)
        report = render_finance_chat_report(
            sim_run_id=sim_run_id, as_of=as_of, start_date=start, end_date=end
        )
        return DomainActionAnswer(
            domain="finance",
            action=action,
            text=f"{start} ~ {end} 재무 보고서를 만들었습니다.",
            data=report,
            report_kind="FINANCE",
        )

    if action == "SALES_PROPOSALS_TODAY":
        value = get_console_sales_proposals(sim_run_id=SHOWN_SIM_RUN_ID, as_of=as_of)
        return DomainActionAnswer(
            domain="sales",
            action=action,
            text=(
                f"오늘 판매안: 제시 가능 {value.presentable_count} · "
                f"검토 필요 {value.review_required_count} · "
                f"판정 대기 {value.unresolved_count} · 확정 불가 {value.rejected_count}"
            ),
            data=dump(value),
        )

    if action == "SALES_CONFIRMED_TODAY":
        value = get_console_sales_proposals(sim_run_id=SHOWN_SIM_RUN_ID, as_of=as_of)
        rows = [dump(row) for row in value.rows if row.sale_status in {"CONFIRMED", "DELIVERED"}]
        return DomainActionAnswer(
            domain="sales",
            action=action,
            text=f"오늘 실제 확정된 판매는 {len(rows)}건입니다.",
            data={"sim_run_id": SHOWN_SIM_RUN_ID, "as_of": as_of.isoformat(), "rows": rows},
        )

    if action == "SALES_REPORT_GENERATE":
        start, end = period_of(intent, as_of=as_of)
        report = render_sales_chat_report(
            sim_run_id=SHOWN_SIM_RUN_ID, as_of=as_of, start_date=start, end_date=end
        )
        return DomainActionAnswer(
            domain="sales",
            action=action,
            text=f"{start} ~ {end} 판매 보고서를 만들었습니다.",
            data=report,
            report_kind="SALES",
        )

    if action == "LOGISTICS_REPORT_GENERATE":
        start, end = period_of(intent, as_of=as_of)
        report = render_logistics_chat_report(
            sim_run_id=SHOWN_SIM_RUN_ID, as_of=as_of, start_date=start, end_date=end
        )
        return DomainActionAnswer(
            domain="logistics",
            action=action,
            text=f"{start} ~ {end} 재고·물류 보고서를 만들었습니다.",
            data=report,
            report_kind="LOGISTICS",
        )

    if action == "PARTNER_LIST":
        value = get_console_partners(
            sim_run_id=SHOWN_SIM_RUN_ID, as_of=as_of, query=slots.partner_ref
        )
        return DomainActionAnswer(
            domain="partner",
            action=action,
            text=f"거래처 {len(value.rows)}곳을 찾았습니다.",
            data=dump(value),
        )

    if action == "PARTNER_DETAIL_GET":
        partner_id = _partner_id(intent, as_of=as_of)
        value = get_console_partner_detail(
            sim_run_id=SHOWN_SIM_RUN_ID, as_of=as_of, partner_id=partner_id
        )
        if value is None:
            raise LookupError("거래처를 찾지 못했습니다.")
        collection_days = value.basic.sales_collection_days
        collection_days_text = "—" if collection_days is None else str(collection_days)
        return DomainActionAnswer(
            domain="partner",
            action=action,
            text=(
                f"{value.basic.partner_name or value.basic.partner_id}\n"
                f"- 유형: {value.basic.partner_type or '—'}\n"
                "- 결제일수: "
                f"{collection_days_text}"
            ),
            data=dump(value),
        )

    raise NotImplementedError(f"{action} 읽기 경로가 배선되지 않았다.")


@contextmanager
def _finance_write() -> Iterator[None]:
    """재무 쓰기 service 가 받지 않은 요청을 ask 의 오류로 옮긴다.

    ★ 화면 재무 라우터와 **같은 상태 코드 · 같은 문장**이다 — 마스터 라우터가 `LookupError` 를
      404 로, `DecisionRejected` 를 409(`conflict=True`) · 422 로 접는다. 2026-09-29 재구성
      BL-014 전에는 재무 라우터 핸들러를 함수로 불러 그 `HTTPException` 이 그대로 나갔다(규칙 1
      위반) — 지금은 같은 재무 service 를 부르고 여기서 옮긴다.
    """
    try:
        yield
    except FinanceWriteRejected as error:
        if error.reason == "NOT_FOUND":
            raise LookupError(error.message) from error
        raise DecisionRejected(error.message, conflict=error.reason == "CONFLICT") from error


def _domain_write(
    intent: Intent,
    *,
    as_of: date,
    policy_version: str,
    request_id: str,
    sim_run_id: str | None = None,
    actor: str,
    utterance: str | None,
) -> DomainActionAnswer:
    action = intent.domain_action or ""
    slots = slots_of(intent)

    if action == "FINANCE_CASH_ADJUSTMENT_CREATE":
        mode = _financing_mode(intent, as_of=as_of)
        direction = slots.direction
        if direction is None:
            raise DomainClarification("입금인지 출금인지 알려주세요.")
        category = slots.cash_category or (
            "OWNER_INJECTION" if direction == "INFLOW" else "OWNER_WITHDRAWAL"
        )
        with core_db.connection() as conn, _finance_write():
            result = apply_cash_adjustment(
                conn,
                CashAdjustmentChange(
                    sim_run_id=SHOWN_SIM_RUN_ID,
                    financing_mode=mode,
                    adjustment_date=as_of,
                    direction=direction,
                    category=category,
                    amount_krw=money(slots.amount, field="금액"),
                    source_ref=slots.source_ref or "",
                    recorded_by=actor,
                    note=slots.note,
                ),
            )
        return DomainActionAnswer(
            domain="finance",
            action=action,
            text=f"자금 변동을 기록했습니다. 현재 현금 {won(result.get('current_cash_krw'))}",
            data=dump(result),
        )

    if action == "FINANCE_CREDIT_LIMIT_UPSERT":
        partner_id = _partner_id(intent, as_of=as_of)
        with core_db.connection() as conn, _finance_write():
            result = change_credit_limit(
                conn,
                CreditLimitChange(
                    partner_id=partner_id,
                    credit_limit_krw=money(
                        slots.credit_limit, field="새 여신한도", allow_zero=True
                    ),
                    effective_from=user_date(
                        slots.effective_from, as_of=as_of, field="적용 시작일", required=True
                    ),
                    evidence_grade=slots.evidence_grade,  # type: ignore[arg-type]
                    source_ref=slots.source_ref or "",
                    recorded_by=actor,
                    note=slots.note,
                ),
            )
        return DomainActionAnswer(
            domain="finance",
            action=action,
            text=(
                f"{partner_id}의 여신한도를 "
                f"{won(result.get('credit_limit_krw'))}으로 저장했습니다."
            ),
            data=dump(result),
        )

    if action == "FINANCE_COLLECTION_CREATE":
        receivable_id = _find_receivable(intent, as_of=as_of)
        mode = _financing_mode(intent, as_of=as_of)
        with core_db.connection() as conn, _finance_write():
            result = record_collection(
                conn,
                ReceivableCollectionChange(
                    sim_run_id=SHOWN_SIM_RUN_ID,
                    financing_mode=mode,
                    collection_date=as_of,
                    receivable_id=receivable_id,
                    collect_all=bool(slots.collect_all),
                    amount_krw=None if slots.collect_all else money(slots.amount, field="수금액"),
                    source_ref=slots.source_ref or "",
                    recorded_by=actor,
                    note=slots.note,
                ),
            )
        return DomainActionAnswer(
            domain="finance",
            action=action,
            text=(
                f"{receivable_id} 수금을 기록했습니다. 남은 받을 돈 "
                f"{won(result.get('outstanding_amount_krw'))}"
            ),
            data=dump(result),
        )

    if action == "FINANCE_EXPENSE_CREATE":
        with core_db.connection() as conn, _finance_write():
            result = accrue_operating_expense(
                conn,
                ExpenseCreate(
                    sim_run_id=SHOWN_SIM_RUN_ID,
                    expense_date=user_date(
                        slots.expense_date, as_of=as_of, field="비용 발생일", required=True
                    ),
                    due_date=user_date(
                        slots.due_date, as_of=as_of, field="지급 예정일", required=True
                    ),
                    expense_category=slots.expense_category or "",
                    amount_krw=money(slots.amount, field="비용 금액"),
                    evidence_id=slots.evidence_id or "",
                    related_delivery_id=slots.related_delivery_id,
                    is_fixed=bool(slots.is_fixed),
                    note=slots.note,
                ),
            )
        return DomainActionAnswer(
            domain="finance",
            action=action,
            text=f"비용 {result.get('expense_id')}을 ACCRUED로 등록했습니다.",
            data=dump(result),
        )

    if action == "FINANCE_EXPENSE_SETTLE":
        mode = _financing_mode(intent, as_of=as_of)
        with core_db.connection() as conn, _finance_write():
            result = pay_accrued_expense(
                conn,
                slots.expense_id or "",
                ExpenseSettle(
                    sim_run_id=SHOWN_SIM_RUN_ID,
                    financing_mode=mode,
                    paid_date=user_date(
                        slots.paid_date, as_of=as_of, field="실제 지급일", required=True
                    ),
                ),
            )
        return DomainActionAnswer(
            domain="finance",
            action=action,
            text=(
                f"{result.get('expense_id')} 비용을 지급했습니다. 현재 현금 "
                f"{won(result.get('current_cash_krw'))}"
            ),
            data=dump(result),
        )

    if action == "FINANCE_EXPENSE_CANCEL":
        with core_db.connection() as conn, _finance_write():
            result = cancel_accrued_expense(
                conn, slots.expense_id or "", ExpenseCancel(sim_run_id=SHOWN_SIM_RUN_ID)
            )
        return DomainActionAnswer(
            domain="finance",
            action=action,
            text=f"{result.get('expense_id')} 비용을 취소했습니다. 현금은 바뀌지 않습니다.",
            data=dump(result),
        )

    if action == "SALES_PROPOSAL_CREATE":
        partner_id = _partner_id(intent, as_of=as_of)
        payload: dict[str, object] = {
            "as_of": as_of,
            "policy_version": policy_version,
            "request_id": request_id,
            "sim_run_id": SHOWN_SIM_RUN_ID,
            "business_mode": slots.business_mode,
            "partner_id": partner_id,
            "item": intent.item,
            "requested_quantity_kg": money(slots.requested_quantity_kg, field="판매 요청 수량"),
            "user_request": utterance,
            "allow_additional_sourcing": bool(slots.allow_additional_sourcing),
        }
        if slots.preferred_unit_price_krw is not None:
            payload["preferred_unit_price_krw"] = money(
                slots.preferred_unit_price_krw, field="희망 단가", allow_zero=True
            )
        if slots.preferred_delivery_date is not None:
            payload["preferred_delivery_date"] = user_date(
                slots.preferred_delivery_date, as_of=as_of, field="납품 희망일", required=True
            )
        if slots.preferred_payment_days is not None:
            payload["preferred_payment_days"] = integer(
                slots.preferred_payment_days, field="결제일수"
            )
        if slots.preferred_payment_terms_type is not None:
            payload["preferred_payment_terms_type"] = slots.preferred_payment_terms_type
        # 권위 있는 source_ref는 채팅이 임의 생성하지 않는다. 없으면 판매/재무가 미비로 남긴다.
        response = run_sales(SalesRunRequest.model_validate(payload))
        return DomainActionAnswer(
            domain="sales",
            action=action,
            text=f"판매 후보 생성을 실행했습니다. 결과: {response.end_code} · {response.reason}",
            data=dump(response),
        )

    if action == "PARTNER_CREATE":
        body: dict[str, object] = {
            "partner_id": slots.partner_id,
            "partner_name": slots.partner_name,
            "partner_type": slots.partner_type,
            "active": True if slots.active is None else slots.active,
        }
        for key in (
            "client_type",
            "factory_region",
            "factory_city",
            "factory_area",
            "pricing_contract_type",
            "note",
        ):
            value = getattr(slots, key)
            if value is not None:
                body[key] = value
        if slots.sales_collection_days is not None:
            body["sales_collection_days"] = integer(slots.sales_collection_days, field="결제일수")
        #  ★ 판매 라우터와 **같은 service** 를 부르고, 같은 문장 · 같은 상태 코드로 거절한다
        #    (409 · 422 — `api/master/ask.py` 가 `DecisionRejected` 를 접는다). 2026-09-29
        #    BL-013 전에는 판매 라우터 핸들러를 함수로 불러 그 `HTTPException` 이 그대로 나갔다.
        try:
            result = create_partner(body)
        except PartnerInputRejected as error:
            raise DecisionRejected(str(error)) from error
        except PartnerAlreadyExists as error:
            raise DecisionRejected(error.message, conflict=True) from error
        return DomainActionAnswer(
            domain="partner",
            action=action,
            text=f"{result.partner_name}({result.partner_id}) 거래처를 등록했습니다.",
            data=dump(result),
        )

    if action == "PARTNER_UPDATE":
        partner_id = _partner_id(intent, as_of=as_of)
        body: dict[str, object] = {}
        for key in (
            "partner_name",
            "partner_type",
            "client_type",
            "factory_region",
            "factory_city",
            "factory_area",
            "pricing_contract_type",
            "active",
            "note",
        ):
            value = getattr(slots, key)
            if value is not None:
                body[key] = value
        if slots.sales_collection_days is not None:
            body["sales_collection_days"] = integer(slots.sales_collection_days, field="결제일수")
        #  ★ 거래처 등록과 같은 규율이다 — 없는 거래처는 404(`LookupError`), 받을 수 없는
        #    입력은 422 로, 판매 라우터와 같은 문장이다.
        try:
            result = update_partner(partner_id, body)
        except PartnerInputRejected as error:
            raise DecisionRejected(str(error)) from error
        except PartnerNotFound as error:
            raise LookupError(error.message) from error
        return DomainActionAnswer(
            domain="partner",
            action=action,
            text=f"{result.partner_name}({result.partner_id}) 거래처 정보를 수정했습니다.",
            data=dump(result),
        )

    raise NotImplementedError(f"{action} 쓰기 경로가 배선되지 않았다.")


def run_domain_action(
    intent: Intent,
    *,
    as_of: date,
    policy_version: str,
    request_id: str,
    sim_run_id: str | None = None,
    actor: str | None = None,
    utterance: str | None = None,
):
    action = intent.domain_action
    if action in DOMAIN_READ_ACTIONS:
        return _domain_read(intent, as_of=as_of, sim_run_id=sim_run_id or SHOWN_SIM_RUN_ID)
    if action in DOMAIN_WRITE_ACTIONS:
        if not actor:
            raise DecisionRejected(
                "기록할 사용자를 확인하지 못했습니다 — 로그인 사용자가 필요합니다."
            )
        return _domain_write(
            intent,
            as_of=as_of,
            policy_version=policy_version,
            request_id=request_id,
            actor=actor,
            utterance=utterance,
        )
    raise NotImplementedError(f"{action} DOMAIN_ACTION 경로가 배선되지 않았다.")
