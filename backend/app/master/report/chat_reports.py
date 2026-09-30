"""채팅 보고서 — 재무 · 판매 · 물류 readmodel 만으로 기간 보고서를 조립한다(LLM · 새 계산 없음).

★ 2026-09-30 재구성 BL-018: `master/report.py` 에서 옮겼다 — `_chat_won`,
  `render_finance_chat_report`, `render_sales_chat_report`, `_logistics_in_scope`, `_logistics_qty`,
  `_logistics_sum`, `_logistics_arrival_display_state`, `_logistics_receipt_rollup`,
  `_logistics_lot_rows`, `render_logistics_chat_report`.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from app.contracts.core import ITEMS
from app.finance.readmodel.console_credit import get_console_credit
from app.finance.readmodel.console_expenses import get_console_expenses
from app.finance.readmodel.console_payables import get_console_payables
from app.finance.readmodel.console_receivables import get_console_receivables
from app.finance.readmodel.dashboard import get_finance_cashflow, get_finance_dashboard
from app.logistics.domain.console_rules import still_working
from app.master.readmodel.logistics_report import read_logistics_report_facts
from app.sales.readmodel.console_partners import get_console_partners
from app.sales.readmodel.console_proposals import get_console_sales_proposals
from app.sales.readmodel.console_trend import get_console_sales_trend
from app.sales.readmodel.dashboard import get_sales_dashboard

# ─── Finance/Sales chat reports (deterministic, LLM 0회) ─────────────────


def _chat_won(value: Any) -> str:
    """Report용 원 표시. None과 0을 절대 합치지 않는다."""
    if value is None:
        return "—"
    try:
        return f"{round(float(value)):,}원"
    except (TypeError, ValueError):
        return "—"


def render_finance_chat_report(*, sim_run_id: str, as_of, start_date, end_date) -> dict[str, Any]:
    """기존 Finance read model만으로 만드는 보고서. LLM/새 계산 없음."""
    # 기말 상태 KPI는 요청 시점이 아니라 보고서 종료일의 동일 실행 read model을 쓴다.
    dashboard = get_finance_dashboard(sim_run_id=sim_run_id, as_of=end_date)
    receivables = get_console_receivables(sim_run_id=sim_run_id, as_of=end_date)
    payables = get_console_payables(sim_run_id=sim_run_id, as_of=end_date)
    expenses = get_console_expenses(
        sim_run_id=sim_run_id,
        as_of=end_date,
        from_date=start_date,
        to_date=end_date,
    )
    credit = get_console_credit(sim_run_id=sim_run_id, as_of=end_date)
    # Exact report range: do not silently cap a user-selected period to a recent horizon.
    days = max(1, (end_date - start_date).days + 1)
    cashflow = get_finance_cashflow(sim_run_id=sim_run_id, as_of=end_date, days=days)
    cashflow_dates = [row.close_date for row in getattr(cashflow, "cashflow", [])]
    dashboard_meta = getattr(dashboard, "meta", None)

    # Finance report 화면·PDF의 정본은 아래 read-model facts다. Markdown 조립은
    # 더 이상 생성 경로가 아니며, 보고서가 표시값을 다시 계산하지 않는다.
    return {
        "kind": "FINANCE",
        "sim_run_id": sim_run_id,
        "as_of": end_date.isoformat(),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        # 요청한 기간과 실제로 읽힌 원장 기간은 다른 사실이다. 데이터가 부족해도
        # 요청 기간을 조용히 바꾸지 않고, 화면이 둘 다 설명할 수 있게 싣는다.
        "available_start_date": min(cashflow_dates).isoformat() if cashflow_dates else None,
        "available_end_date": max(cashflow_dates).isoformat() if cashflow_dates else None,
        "data_mode": getattr(dashboard_meta, "data_type", None),
        "summary": dashboard.model_dump(mode="json"),
        "cashflow": cashflow.model_dump(mode="json"),
        # The chart/table must use the same exact report-range read as the availability
        # metadata. Dashboard recent_closings is intentionally a short console preview.
        "closings": [row.model_dump(mode="json") for row in getattr(cashflow, "cashflow", [])],
        "receivables": receivables.model_dump(mode="json"),
        "payables": payables.model_dump(mode="json"),
        "expenses": expenses.model_dump(mode="json"),
        "credit": credit.model_dump(mode="json"),
    }

    lines = [
        "# 재무 보고서",
        "",
        f"- 기준 실행: `{sim_run_id}`",
        f"- 기간: {start_date.isoformat()} ~ {end_date.isoformat()}",
        "",
        "## 현재 자금",
    ]
    if dashboard.states:
        for state in dashboard.states:
            lines.append(
                f"- {state.financing_mode}: 현재 현금 {_chat_won(state.current_cash_krw)}"
                f" · 운영 여유 {_chat_won(state.operating_cash_buffer_krw)}"
                f" · 차입 잔액 {_chat_won(state.current_debt_krw)}"
            )
    else:
        lines.append("- 기록 없음")

    rs = receivables.summary
    ps = payables.summary
    es = expenses.summary
    lines += [
        "",
        "## 채권 · 채무",
        f"- 아직 받을 돈: {_chat_won(rs.total_outstanding_krw)}",
        f"- 연체 1~7일: {_chat_won(rs.days_1_7_krw)}",
        f"- 연체 8~30일: {_chat_won(rs.days_8_30_krw)}",
        f"- 연체 30일 초과: {_chat_won(rs.days_30_plus_krw)}",
        f"- 아직 지급할 매입대금: {_chat_won(ps.total_outstanding_krw)}",
        f"- 오늘 지급 예정: {_chat_won(ps.due_today_krw)}",
        f"- 7일 내 지급 예정: {_chat_won(ps.due_next_7d_krw)}",
        f"- 연체 매입대금: {_chat_won(ps.overdue_krw)}",
        "",
        "## 비용",
        f"- 미지급(ACCRUED): {_chat_won(es.accrued_krw)} · {es.accrued_count}건",
        f"- 지급(PAID): {_chat_won(es.paid_krw)}",
        f"- 취소(CANCELLED): {_chat_won(es.cancelled_krw)}",
        "",
        "## 거래처 여신",
    ]
    if credit.partners:
        for row in credit.partners:
            name = row.partner_name or row.partner_id
            lines.append(
                f"- {name}: 한도 {_chat_won(row.credit_limit_krw)}"
                f" · 미수 {_chat_won(row.current_ar_krw)}"
                f" · 가용 {_chat_won(row.available_credit_krw)}"
            )
    else:
        lines.append("- 표시할 거래처 여신 기록 없음")

    lines += ["", "## 일마감 현금 흐름"]
    if dashboard.recent_closings:
        for row in dashboard.recent_closings:
            lines.append(
                f"- {row.close_date}: 수금 {_chat_won(row.collection_cash_in_krw)}"
                f" · 매입 {_chat_won(row.purchase_cash_out_krw)}"
                f" · 물류 {_chat_won(row.logistics_cash_out_krw)}"
                f" · 급여·이자 {_chat_won(row.payroll_interest_cash_out_krw)}"
                f" · 운영비 {_chat_won(row.operating_expense_cash_out_krw)}"
                f" · 순현금 {_chat_won(row.base_net_cash_krw)}"
            )
    else:
        lines.append("- 기록 없음")

    return {
        "kind": "FINANCE",
        "sim_run_id": sim_run_id,
        "as_of": as_of.isoformat(),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "summary": dashboard.model_dump(mode="json"),
        "cashflow": cashflow.model_dump(mode="json"),
        "closings": [row.model_dump(mode="json") for row in dashboard.recent_closings],
        "receivables": receivables.model_dump(mode="json"),
        "payables": payables.model_dump(mode="json"),
        "expenses": expenses.model_dump(mode="json"),
        "credit": credit.model_dump(mode="json"),
    }


def render_sales_chat_report(*, sim_run_id: str, as_of, start_date, end_date) -> dict[str, Any]:
    """기존 Sales read model만으로 만드는 보고서. LLM/재계산 없음."""
    dashboard = get_sales_dashboard(sim_run_id=sim_run_id, as_of=as_of)
    proposals = get_console_sales_proposals(sim_run_id=sim_run_id, as_of=as_of)
    days = max(1, min(400, (end_date - start_date).days + 1))
    trend = get_console_sales_trend(
        sim_run_id=sim_run_id,
        as_of=as_of,
        days=days,
        from_date=start_date,
        to_date=end_date,
    )
    partners = get_console_partners(sim_run_id=sim_run_id, as_of=as_of)
    trend_dates = [row.sale_date for row in trend.rows]
    dashboard_meta = getattr(dashboard, "meta", None)

    # 판매 보고서도 저장된 sales/read-model facts만 전달한다. 확정 여부는
    # sale_status가 실제 CONFIRMED·DELIVERED인 행만으로 제한한다.
    return {
        "kind": "SALES",
        "sim_run_id": sim_run_id,
        "as_of": as_of.isoformat(),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "available_start_date": min(trend_dates).isoformat() if trend_dates else None,
        "available_end_date": max(trend_dates).isoformat() if trend_dates else None,
        "data_mode": getattr(dashboard_meta, "data_type", None),
        "summary": dashboard.model_dump(mode="json"),
        "proposals": proposals.model_dump(mode="json"),
        "trend": trend.model_dump(mode="json"),
        "partners": partners.model_dump(mode="json"),
        "confirmed_sales": [
            row.model_dump(mode="json")
            for row in proposals.rows
            if row.sale_status in {"CONFIRMED", "DELIVERED"}
        ],
        "confirmed_count": sum(
            1 for row in proposals.rows if row.sale_status in {"CONFIRMED", "DELIVERED"}
        ),
    }

    lines = [
        "# 판매 보고서",
        "",
        f"- 기준 실행: `{sim_run_id}`",
        f"- 기간: {start_date.isoformat()} ~ {end_date.isoformat()}",
        "",
        "## 금일 판매 후보",
        f"- 전체 화면 상태: {proposals.state}",
        f"- 제시 가능: {proposals.presentable_count}건",
        f"- 검토 필요: {proposals.review_required_count}건",
        f"- 판정 대기: {proposals.unresolved_count}건",
        f"- 확정 불가: {proposals.rejected_count}건",
    ]

    confirmed = [row for row in proposals.rows if row.sale_status in {"CONFIRMED", "DELIVERED"}]
    lines += ["", "## 금일 확정 판매", f"- {len(confirmed)}건"]
    for row in confirmed:
        lines.append(
            f"- {row.item or '품목 미상'} · {row.partner_id or '거래처 미상'}"
            f" · {row.quantity_kg if row.quantity_kg is not None else '—'}kg"
            f" · 매출 {_chat_won(row.reported_sales_amount_krw)}"
            f" · 상태 {row.sale_status}"
        )

    lines += ["", "## 기간 판매 추이"]
    if trend.rows:
        for row in trend.rows:
            lines.append(
                f"- {row.sale_date}: {row.sales_count}건"
                f" · {row.quantity_kg}kg"
                f" · 매출 {_chat_won(row.sales_amount_krw)}"
                f" · 공헌이익 {_chat_won(row.contribution_profit_krw)}"
            )
    else:
        lines.append("- 기록 없음")

    lines += ["", "## 금일 전략 · 검증"]
    if proposals.rows:
        for row in proposals.rows:
            strategy = row.strategy
            strategy_text = (
                "전략 기록 없음"
                if strategy is None
                else f"{strategy.source or '—'} / LLM {strategy.llm_status or '—'}"
            )
            lines.append(
                f"- {row.scenario_type or row.scenario_id}: {row.presentation_state}"
                f" · 재무 {row.finance_verdict or '—'}"
                f" · {strategy_text}"
            )
            if row.presentation_state == "UNRESOLVED" and row.unresolved_reason_codes:
                lines.append("  - 미판정: " + ", ".join(row.unresolved_reason_codes))
    else:
        lines.append("- 후보 없음")

    return {
        "kind": "SALES",
        "sim_run_id": sim_run_id,
        "as_of": as_of.isoformat(),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "summary": dashboard.model_dump(mode="json"),
        "proposals": proposals.model_dump(mode="json"),
        "trend": trend.model_dump(mode="json"),
        "partners": partners.model_dump(mode="json"),
        "confirmed_sales": [
            row.model_dump(mode="json")
            for row in proposals.rows
            if row.sale_status in {"CONFIRMED", "DELIVERED"}
        ],
        "confirmed_count": sum(
            1 for row in proposals.rows if row.sale_status in {"CONFIRMED", "DELIVERED"}
        ),
    }


# ─── 재고·물류 chat report (deterministic, LLM 0회) ────────────────────────


def _logistics_in_scope(name: Any, items: tuple[str, ...]) -> bool:
    """이 행을 보고서 품목 칸에 싣는가. **계약 `ITEMS` 하나가 기준이다.**

    🔴 **제외 품목 이름을 여기 적지 않는다.** 피마늘·건고추는 «현재 프로젝트 범위 밖»
       이라는 업무 결정이고, 그 결정의 주인은 `contracts.core.ITEMS` 다. 여기에 이름을
       또 적으면 범위가 바뀔 때 두 곳이 갈린다.

    ★ **이름을 못 읽은 행(`None`)은 남긴다.** 이름 미상과 범위 밖은 다른 사실이다 —
      물류 화면이 지키는 원칙 그대로다.
    """
    return name is None or str(name) in items


def _logistics_qty(value: Any) -> str | None:
    """수량 한 칸. 🔴 **`None` 을 0 으로 메우지 않는다.**

    못 읽은 것과 0 kg 은 다른 사실이다. 자릿수를 잃지 않게 `Decimal` 문자열 그대로 둔다 —
    화면 `reportFormat.kg()` 가 `Number()` 로 읽는다 (Finance/Sales facts 와 같은 모양).
    """
    return None if value is None else str(value)


def _logistics_sum(values: list[Any]) -> Any:
    """수량 합. 🔴 **한 칸이라도 `None` 이면 합계도 `None` 이다.**

    아는 값만 더해 숫자를 만들면 «모르는 값이 0 이었다» 고 말하는 것과 같다.
    판매가능량 합계가 지키는 규율을 입고 실적 집계에도 그대로 쓴다.
    """
    if any(value is None for value in values):
        return None
    return sum(values, start=Decimal(0))


def _logistics_arrival_display_state(expected_arrival_date: Any, as_of: Any) -> str | None:
    """도착 전 물량 한 줄의 **화면 표기용** 도착 상태. 🔴 업무 판정이 아니다.

    ```text
    None            예정일을 모른다        → 화면은 「—」
    SCHEDULED       예정일이 아직 안 왔다
    OVERDUE         예정일이 지났는데 아직 안 왔다
    ```

    🔴 **`arrival.select_due_inbound` 의 네 갈래(due · blocked · not_due · unresolved)를
       여기서 흉내 내지 않는다.** 그 판정의 주인은 물류이고, 결과는 이미
       `arrival_summary` 카드로 나간다. 그 함수를 이 목록에 다시 돌리면
       **아직 안 켜진 `purchase_id` 참조** 때문에 정상 건이 전부 「막힘」으로 찍힌다
       (`schemas.InTransitItem` · `arrival.select_due_inbound` 주석).

    ★ 그래서 여기서 보는 것은 **예정일이 지났나** 하나뿐이다. 「아직 Receipt 가 없다」는
      사실은 이 목록의 모집단 자체가 이미 보장한다 — 새 상태를 만드는 것이 아니다.
    """
    if expected_arrival_date is None:
        return None
    return "SCHEDULED" if expected_arrival_date > as_of else "OVERDUE"


def _logistics_receipt_rollup(receipts: list[Any]) -> list[dict[str, Any]]:
    """Receipt 원장을 **「입고일 + 품목」 기간 실적**으로 접는다.

    ★ **단순 합산이지 업무 판정이 아니다.** 상태를 새로 매기지 않고, 검수 결과는
      그 묶음에 실제로 있던 값들을 **그대로 나열**한다 — 섞여 있으면 하나로
      뭉뚱그리지 않는다.

    🔴 **`None` 수량을 0 으로 세지 않는다** (`_logistics_sum`).

    ★ 정렬은 최신 입고일 먼저, 같은 날은 품목 이름순이다 (지시 §27).
    """
    groups: dict[tuple[Any, str], list[Any]] = {}
    for receipt in receipts:
        key = (receipt.arrived_at, receipt.item_name or receipt.item_id)
        groups.setdefault(key, []).append(receipt)

    out: list[dict[str, Any]] = []
    for (arrived_at, item), rows_in_group in groups.items():
        verdicts = sorted({r.inspection_verdict for r in rows_in_group if r.inspection_verdict})
        out.append(
            {
                "arrived_at": arrived_at.isoformat(),
                "item": item,
                "receipt_count": len(rows_in_group),
                "ordered_qty_kg": _logistics_qty(
                    _logistics_sum([r.ordered_qty_kg for r in rows_in_group])
                ),
                "accepted_qty_kg": _logistics_qty(
                    _logistics_sum([r.accepted_qty_kg for r in rows_in_group])
                ),
                "hold_qty_kg": _logistics_qty(
                    _logistics_sum([r.hold_qty_kg for r in rows_in_group])
                ),
                "rejected_qty_kg": _logistics_qty(
                    _logistics_sum([r.rejected_qty_kg for r in rows_in_group])
                ),
                #: 그 묶음에 있던 검수 결과들. 판정을 지어내지 않는다.
                "inspection_verdicts": verdicts,
                #: 아직 검수 결과가 없는 건수. 「0건」과 「모름」을 가른다.
                "inspection_unknown_count": sum(
                    1 for r in rows_in_group if not r.inspection_verdict
                ),
                #: 재고가 실제로 선 완료 건수.
                "stock_applied_count": sum(1 for r in rows_in_group if r.stock_applied),
                #: 🔴 **«반영할 재고 없음» 도 완료다** (#805). 수용 0 으로 끝난 건은
                #:    재고가 안 생길 뿐 처리가 끝난 것이라, 「재고 반영 N/M건」 한 칸만
                #:    내리면 미처리 건으로 잘못 읽힌다.
                #:
                #: 🔴 **여기서 다시 판정하지 않는다.** 정본은
                #:    `inbound_schedules.InboundScheduleView.settled_without_stock` 이고
                #:    콘솔이 `inbound_id` 로 받아 적은 값을 그대로 센다. `accepted_qty_kg
                #:    == 0` 이나 `receipt_status` 로 재계산하면 경계에서 콘솔과 갈린다.
                "settled_without_stock_count": sum(
                    1 for r in rows_in_group if r.settled_without_stock is True
                ),
                #: 🔴 **`None` 은 «모른다» 다 — `False` 가 아니다.** 그날 입고 일정을 못
                #:    읽었으면 「반영 대기」인지 「반영할 재고 없음」인지 가릴 수 없다.
                #:    「0건」과 「모름」을 가르는 `inspection_unknown_count` 와 같은 규율이다.
                #:
                #: ★ **`stock_applied` 가 참이면 모름이 아니다.** 재고가 이미 섰으므로
                #:   어느 완료인지 알고 있다 — 그 건까지 「확인 못 함」으로 세면 아는
                #:   사실을 모른다고 말하는 것이 된다.
                #:
                #: 🔴 **`pending` 을 지어내지 않는다.** `receipt_count` 에서 둘을 빼면
                #:    검수 전 건과 못 읽은 건까지 「재고 반영 대기」로 단정하게 된다 —
                #:    그 판정의 근거가 이 자리에 없다.
                "settled_unknown_count": sum(
                    1
                    for r in rows_in_group
                    if not r.stock_applied and r.settled_without_stock is None
                ),
            }
        )
    out.sort(key=lambda row: (row["arrived_at"], row["item"]), reverse=True)
    return out


def _logistics_lot_rows(lots: list[Any]) -> list[dict[str, Any]]:
    """Lot 을 **사용자 표시 순서**로 늘어놓고 표시용 순번을 붙인다.

    ★ **raw `lot_id` 를 쪼개 뜻을 캐내지 않는다.** 표시명은 `item_name` 과
      `received_at` 구조화 칸으로 화면이 만든다. 같은 품목·같은 입고일 Lot 이 여럿이면
      `lot_id` 정렬로 **안정된 순번**(`display_index`)만 여기서 매긴다.

    ★ 순서는 폐기 검토 → 우선 출고 → 신선도 잔여 적은 순 → 입고일 오래된 순이다
      (지시 §27). **표시 순서일 뿐 업무 판정이 아니다** — 값은 read model 것 그대로다.
    """
    groups: dict[tuple[Any, Any], list[Any]] = {}
    for lot in lots:
        groups.setdefault((lot.item_name or lot.item_id, lot.received_at), []).append(lot)
    seq: dict[str, tuple[int, int]] = {}
    for members in groups.values():
        ordered = sorted(members, key=lambda lot: lot.lot_id)
        for index, lot in enumerate(ordered, start=1):
            seq[lot.lot_id] = (index, len(ordered))

    def _order(lot: Any) -> tuple[Any, ...]:
        fresh = lot.remaining_freshness_days
        return (
            not lot.disposal_candidate,
            not lot.sell_priority,
            # 🔴 `None` 은 0 이 아니다 — 모르는 값을 «가장 급한 것» 으로 올리지 않는다.
            (1, 0) if fresh is None else (0, fresh),
            lot.received_at,
            lot.lot_id,
        )

    out: list[dict[str, Any]] = []
    for lot in sorted(lots, key=_order):
        index, size = seq[lot.lot_id]
        row = lot.model_dump(mode="json")
        row["display_index"] = index
        row["display_group_size"] = size
        out.append(row)
    return out


def render_logistics_chat_report(
    *,
    sim_run_id: str,
    as_of,
    start_date,
    end_date,
) -> dict[str, Any]:
    """기존 재고·물류 read model 만으로 만드는 보고서. **LLM·새 계산·새 SQL 0.**

    ```text
    기준일 재고 · 창고 사용량   get_inventory_console                        ← as_of 원장
    입고 · 검수                 get_inbound_console
    예약 · 출고                 get_outbound_console                         ← 같은 예약 한 벌
    기간 재고 추이              onhand_total_by_day + snapshot_days_between
    ```

    🔴 **여기서 업무를 새로 판정하지 않는다.** 신선도·회전·예약 상태·Receipt 상태는
       전부 read model 이 `as_of` 축에서 낸 값을 받아 적기만 한다. 보고서가 판정을
       시작하면 화면과 문서가 **다른 상태**를 말하게 된다.

    🔴 **커넥션은 한 보고서에 하나다.** `reservation_state_at` 도 한 번만 읽어 재고
       콘솔과 출고 콘솔이 나눠 쓴다 — 화면(`api/logistics/presenter.build_result`)과 같은
       조립 순서다. (2026-09-30 재구성 BL-018: 그 조회 조립 · 연결 대여는
       `readmodel/logistics_report.read_logistics_report_facts` 로 옮겼다 — 여기는 문장만 짓는다.)

    🔴 **창고 사용량을 표시 품목 합으로 다시 만들지 않는다.** 실제 창고 점유는 계약 밖
       품목까지 포함한 «그날 실재한 모든 Lot» 의 합이라 표시 품목 합과 다를 수 있다.
    """
    facts = read_logistics_report_facts(
        sim_run_id=sim_run_id, as_of=as_of, start_date=start_date, end_date=end_date
    )
    inventory, inbound, outbound = facts.inventory, facts.inbound, facts.outbound
    series, opened = facts.series, facts.opened

    items = [row for row in inventory.items if _logistics_in_scope(row.item_name, ITEMS)]
    lots = [row for row in inventory.lots if _logistics_in_scope(row.item_name, ITEMS)]
    # ★ 내부 이름 `in_transit` 은 **차량 위치 추적이 아니다.** 「입고 일정에 올라 있고 아직
    #   Receipt 가 안 선 건」 = 도착 전 물량이다 (`get_inbound_console` 머리말).
    #   `None`(그날 목록을 확인 못 했다)과 `[]`(0건 확인)은 다른 값이라 그대로 가른다.
    in_transit = (
        None
        if inbound.in_transit is None
        else [row for row in inbound.in_transit if _logistics_in_scope(row.item, ITEMS)]
    )

    # 🔴 **Receipt 는 기간 발생 내역이다 — 기준일 Snapshot 이 아니다.**
    #    `receipt_state_at` 은 그날까지 도착한 **전체 이력**을 낸다(실측 289건). 하루짜리
    #    보고서에 1월 입고가 딸려 나오던 자리라, 보고 기간 안에 도착한 것만 남긴다.
    receipts = [
        row
        for row in inbound.receipts
        if _logistics_in_scope(row.item_name, ITEMS) and start_date <= row.arrived_at <= end_date
    ]

    # 🔴 **예약은 «그날 아직 일이 남은 것» 만 본문에 싣는다.** 모집단 정의의 주인은
    #    물류 domain(`logistics/domain/console_rules.still_working` · #675 §10)이고 화면과
    #    같은 함수를 부른다. 여기서 새로 적지 않는다 — 두 벌로 적으면 한쪽만 고쳐지는 날이
    #    온다. 전량 출고가 끝난 과거 예약과 SHIPPED 할당 이력을 수개월치 늘어놓지 않는다.
    #    (2026-09-29 재구성 BL-012 전에는 화면 모듈의 비공개 `_still_working` 을 빌려 썼다.)
    scoped_reservations = [
        row for row in outbound.reservations if _logistics_in_scope(row.item_name, ITEMS)
    ]
    working = [row for row in scoped_reservations if still_working(row)]
    # ★ 납기일 빠른 순. 납기일이 없는 예약은 뒤로 둔다 (지시 §27).
    working.sort(key=lambda row: (row.due_date is None, row.due_date, row.reservation_id))
    settled_count = len(scoped_reservations) - len(working)

    # ★ 화면 표시용 단순 합계까지만 한다 — 업무 공식을 새로 만들지 않는다.
    #   🔴 판매가능량은 한 칸이라도 못 읽었으면 전체도 못 읽은 것이다. 아는 값만 더해
    #      숫자를 만들면 «모르는 값이 0 이었다» 고 말하는 것과 같다.
    total_available = _logistics_sum([row.available_qty_kg for row in items])
    total_on_hand = sum((row.on_hand_qty_kg for row in items), start=Decimal(0))
    total_unallocated = sum((row.unallocated_reserved_qty_kg for row in items), start=Decimal(0))

    # 🔴 기간 추이: **시뮬레이션이 안 연 날은 `null` 이다 — 0kg 이 아니다.**
    #    원장 누계는 어떤 날짜에도 숫자를 내고 첫 사실 이전 구간에서 그 값이 0 인데,
    #    그 0 은 «재고가 없다» 가 아니라 «그날을 모른다» 다
    #    (화면 `presenter._onhand_series` 와 같은 규칙).
    trend: list[dict[str, Any]] = []
    for offset in range((end_date - start_date).days + 1):
        day = start_date + timedelta(days=offset)
        known = day in opened and day in series
        trend.append(
            {"date": day.isoformat(), "on_hand_qty_kg": float(series[day]) if known else None}
        )

    #  ★ 공용 머리말(`ReportChrome`)이 «요청 기간 vs 실제 데이터 기간» 을 설명하는 칸.
    #    🔴 **새로 재지 않는다** — 위 `trend` 가 이미 «열린 날만 값» 이라, 값이 있는 날의
    #       처음과 끝이 그대로 실제 데이터 범위다. 값이 하나도 없으면 `None` 이고
    #       그것도 사실이다 (0 일짜리 범위를 지어내지 않는다).
    covered = [row["date"] for row in trend if row["on_hand_qty_kg"] is not None]

    capacity = inventory.capacity
    return {
        "kind": "LOGISTICS",
        "sim_run_id": sim_run_id,
        "as_of": as_of.isoformat(),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "available_start_date": covered[0] if covered else None,
        "available_end_date": covered[-1] if covered else None,
        #: 🔴 **물류에는 권위 있는 «데이터 모드» 가 없다.** 재무는
        #:    `dashboard.meta.data_type` 에서 오는데 물류 read model 에는 대응하는 칸이
        #:    없다. 새 문자열을 지어내면 화면이 **근거 없는 설명**을 하게 되므로 `None`
        #:    으로 둔다 — 공용 머리말이 그때 「실행 모드 미확인」으로 적는다.
        "data_mode": None,
        #: ★ **기준일 Snapshot 인가 기간 발생 내역인가를 칸 이름으로 가른다.**
        #:   `summary` · `inventory` · `outbound` 는 `as_of` 상태이고,
        #:   `inbound.period_*` 와 `trend` 는 `start_date~end_date` 에 일어난 일이다.
        "summary": {
            "item_count": len(items),
            "total_on_hand_qty_kg": _logistics_qty(total_on_hand),
            "total_available_qty_kg": _logistics_qty(total_available),
            "total_reserved_qty_kg": _logistics_qty(
                sum((row.reserved_qty_kg for row in items), start=Decimal(0))
            ),
            "total_unallocated_reserved_qty_kg": _logistics_qty(total_unallocated),
            #: 판매가능량이 `None` 인 이유. read model 이 낸 값을 그대로 옮긴다.
            "available_qty_unresolved_reason": inventory.available_qty_unresolved_reason,
            #: 🔴 창고 Capacity 는 read model 값 그대로다 — 품목 카드 합이 아니다.
            "used_capacity_kg": _logistics_qty(capacity.used_capacity_kg),
            "guaranteed_capacity_kg": _logistics_qty(capacity.guaranteed_capacity_kg),
            "burst_capacity_kg": _logistics_qty(capacity.burst_capacity_kg),
            "capacity_basis": capacity.capacity_basis,
            "lot_count": len(lots),
            #: Lot 건수는 품목 카드가 이미 센 값을 더한 것이다 — 여기서 다시 판정하지 않는다.
            #: ⚠️ 이 read model 에서 «만료 Lot» 과 «폐기 검토 Lot» 은 **같은 모집단**이라
            #:    (`console_service`: `disposal_candidate and remaining > 0`) 한 칸만 낸다.
            "sell_priority_lot_count": sum(row.sell_priority_lot_count for row in items),
            "disposal_candidate_lot_count": sum(row.disposal_candidate_lot_count for row in items),
            "working_reservation_count": len(working),
            "settled_reservation_count": settled_count,
            #: 🔴 단순 사실 비교다 — 새 KPI 도 severity 도 아니다 (지시 §30).
            #:    `Decimal` 로 재서 문자열 비교의 오차를 남기지 않는다.
            "unallocated_exceeds_on_hand": bool(total_unallocated > total_on_hand),
            "trend_is_single_day": start_date == end_date,
        },
        "inventory": {
            "items": [row.model_dump(mode="json") for row in items],
            #: 🔴 표시 순서와 표시용 순번만 붙인 Lot. 값은 read model 것 그대로다.
            "lots": _logistics_lot_rows(lots),
            "capacity": capacity.model_dump(mode="json"),
            "available_qty_unresolved_reason": inventory.available_qty_unresolved_reason,
        },
        "inbound": {
            "arrival_summary": inbound.arrival_summary.model_dump(mode="json"),
            "in_transit_status": inbound.in_transit_status,
            #: 도착 전 물량. 내부 이름은 계약대로 `in_transit` 이고 화면 표시명만 「입고 예정」이다.
            "in_transit": (
                None
                if in_transit is None
                else [
                    dict(
                        row.model_dump(mode="json"),
                        arrival_display_state=_logistics_arrival_display_state(
                            row.expected_arrival_date, as_of
                        ),
                    )
                    for row in in_transit
                ]
            ),
            #: 보고 기간에 도착한 Receipt 를 「입고일 + 품목」으로 접은 실적. **본문용.**
            "period_receipt_rollup": _logistics_receipt_rollup(receipts),
            "period_receipt_count": len(receipts),
            #: 🔴 추적용 원장은 facts 에 **남긴다.** 화면이 안 그릴 뿐이다 (지시 §10).
            "period_receipts": [row.model_dump(mode="json") for row in receipts],
        },
        "outbound": {
            #: 🔴 «그날 아직 일이 남은» 예약만. 전량 출고가 끝난 과거 예약은 건수로만 남긴다.
            "working_reservations": [
                dict(
                    row.model_dump(mode="json"),
                    #: 할당 Lot 수 = `allocated_qty_kg` 와 **같은 모집단**(아직 안 나간 할당)이다.
                    allocation_lot_count=len(
                        {a.lot_id for a in row.allocations if a.status == "ALLOCATED"}
                    ),
                )
                for row in working
            ],
            "working_reservation_count": len(working),
            "settled_reservation_count": settled_count,
        },
        "trend": trend,
    }
