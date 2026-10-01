"""그날 승인에 적힌 **실매입 합계** — 화면 둘(대시보드 · 매입 탭)이 읽는다.

```text
repository   purchase_record_repository.recorded_sums_by_plan   (품목, 안 이름)마다 수량 · 금액 합
readmodel    recorded_totals_by_plan (여기)                     → {(품목, 안 이름): RecordedTotals}
화면         api/dashboard/presenter._records · api/purchase/presenter._records
             못 읽으면 빈 표로 두고 화면을 띄운다 (그 태도는 화면이 정한다)
```

🟢 2026-09-29 (재구성 BL-012) 전에는 `RecordedTotals` 와 이 조립이 SQL 과 한 함수
   (`master/purchase_record_repository.recorded_totals_by_plan`)에 있었고, 화면이 그
   repository 를 직접 불렀다. SQL 은 repository 에 두고 결과 모델과 단가 계산을 여기로
   옮겼다. 함수 이름 · 인자 · 돌려주는 값은 그대로다.

★ 2026-09-30 재구성 BL-018: 실매입 기록 조회를 여기로 모았다 — 화면용 `get_purchase_record` 와
  기록을 걸 승인 조회 `approval_to_record`(`master/purchase_record.py` 에서 · 기록 service 도 이것을
  부른다), 기록된 승인 키 · 마지막 마감일 조회(기록 행 조회 `list_purchase_record_legs` 는
  `readmodel/approvals.py` — 현재 승인 조립이 먼저 쓴다). 종전에는 `master/db.py` 의 `fetch_all` 이
  조회마다 조회 연결을 스스로 빌렸다 — 여기 공개 함수가 조회 하나에 조회 연결 하나를 빌려
  `repository/purchase_records.py` 의 `select_*` 에 넘긴다(횟수 · 종류 같음).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.core import db as core_db
from app.core.settings import get_db_schema
from app.master.domain.decision import PROCUREMENT_CYCLE, awaits_purchase_record
from app.master.domain.purchase_ids import purchase_id_prefix_for
from app.master.domain.purchase_record import plan_unit_price, recorded_unit_price
from app.master.readmodel.approvals import current_approval, list_purchase_record_legs
from app.master.readmodel.pending_transitions import ledger_purchase_ids
from app.master.repository.purchase_records import (
    select_last_closed_date,
    select_recorded_decision_keys,
    select_recorded_sums_by_plan,
)
from app.master.schemas.approval import CurrentApproval
from app.master.schemas.decision import DecisionRejected
from app.master.schemas.purchase_record import (
    PurchaseRecordLegOut,
    PurchaseRecordOut,
    PurchaseRecordPlanOut,
    PurchaseRecordValuesOut,
)


@dataclass(frozen=True)
class RecordedTotals:
    """한 승인에 적힌 실매입의 **합계**. 회차가 여럿이면 그 합이다.

    ★ 안의 제안값과 **다른 사실**이다. 「사자고 낸 값」이 아니라 「실제로 산 값」이다.

    🔴 `unit_price` 의 `None` 은 «단가가 없다» 가 아니라 **«정수 단가로 안 떨어진다»** 다.
       금액은 사람이 실제로 낸 돈이라 그것이 정본이고, 나누어떨어지지 않는다고 반올림해
       보이면 화면의 `단가 × 수량` 이 금액 칸과 어긋난다 — `purchase_record._grade_lines`
       가 같은 이유로 금액 대신 줄을 하나 더 얹는다.
    """

    qty_kg: float
    amount_krw: int
    unit_price: int | None


def _totals(quantity_kg: Any, amount_krw: Any) -> RecordedTotals:
    """합계 두 개에서 단가까지. **정수 나눗셈으로만** 센다 — 부동소수로 어림하지 않는다."""
    qty = float(quantity_kg)
    amount = round(float(amount_krw))
    unit: int | None = None
    if qty.is_integer() and int(qty) > 0 and amount % int(qty) == 0:
        unit = amount // int(qty)
    return RecordedTotals(qty_kg=qty, amount_krw=amount, unit_price=unit)


def recorded_totals_by_plan(
    *, sim_run_id: str, as_of: date
) -> dict[tuple[str, str], RecordedTotals]:
    """그 실행 축 · 그날 승인에 적힌 실매입 합계. 열쇠는 `(품목, 안 이름)`.

    열쇠를 왜 그 둘로 잡는지, 축과 기준일을 왜 둘 다 거는지는 SQL 자리
    (`purchase_record_repository.recorded_sums_by_plan`)에 적혀 있다.

    ⚠️ 적힌 기록이 없으면 **빈 표**다. 0 으로 채우지 않는다 — «안 샀다» 와 «못 읽었다» 는
      부르는 쪽이 가린다. 읽다가 난 예외도 그대로 올려 보낸다.
    """
    return {
        (str(row["item"]), str(row["scenario_label"])): _totals(
            row["quantity_kg"], row["amount_krw"]
        )
        for row in _recorded_sums(sim_run_id=sim_run_id, as_of=as_of)
    }


def _recorded_sums(*, sim_run_id: str, as_of: date) -> list[dict[str, Any]]:
    schema = get_db_schema()
    with core_db.read_connection() as conn:
        return select_recorded_sums_by_plan(conn, sim_run_id=sim_run_id, as_of=as_of, schema=schema)


def recorded_decision_keys(*, sim_run_id: str) -> list[tuple[str, int]]:
    """이 실행 축에서 **기록이 있는 승인** `(request_id, decision_seq)` 전부.

    ★ 재시도가 *"사람 승인인데 기록이 없다"* 를 거르는 데 쓴다
      (`domain/pending_transition.pending_approvals`).
    """
    schema = get_db_schema()
    with core_db.read_connection() as conn:
        return select_recorded_decision_keys(conn, sim_run_id=sim_run_id, schema=schema)


def last_closed_date(*, sim_run_id: str) -> date | None:
    """그 실행 축의 **마지막 재무 일마감일**. 마감이 없으면 `None`.

    ★ **읽기만 한다.** `daily_closings` 의 주인은 재무다 (`finance/service/closing.py` 가 적는다).
      실매입 매입일이 이미 마감된 날로 들어가지 못하게 막는 데만 쓴다
      (설계 260915 안 A §4-6 ②).
    """
    schema = get_db_schema()
    with core_db.read_connection() as conn:
        return select_last_closed_date(conn, sim_run_id=sim_run_id, schema=schema)


def approval_to_record(request_id: str) -> CurrentApproval:
    """기록을 걸 **현재 승인**. 없으면 `LookupError` (라우터가 404)."""
    approval = current_approval(request_id)
    if approval is None:
        raise LookupError(f"업무 키 {request_id} 에 유효한 승인이 없다 — 실매입은 승인에만 적는다.")
    if approval.cycle and approval.cycle != PROCUREMENT_CYCLE:
        raise DecisionRejected(
            f"업무 키 {request_id} 의 승인은 매입 승인이 아니다 — 실매입을 적을 수 없다.",
            conflict=True,
        )
    return approval


def get_purchase_record(request_id: str) -> PurchaseRecordOut:
    """화면용 — 선정안 회차(기본값) · 기록(있으면) · 반영 상태.

    :raises LookupError: 승인이 없다 (404).
    """
    approval = approval_to_record(request_id)
    decision = approval.decision
    plan = approval.plan
    단가 = plan_unit_price(plan)
    plan_out = PurchaseRecordPlanOut(
        grade=plan.grades[0] if plan is not None and plan.grades else None,
        legs=[
            PurchaseRecordLegOut(
                seq=leg.seq,
                qty_kg=leg.qty_kg,
                unit_price_krw=단가,
                amount_krw=leg.amount_krw,
                purchase_date=leg.purchase_date,
                arrival_date=leg.arrival_date,
            )
            for leg in (plan.arrival_schedule if plan is not None else ())
        ],
    )
    base = {
        "request_id": request_id,
        "decision_seq": decision.decision_seq,
        "scenario_label": decision.scenario_label,
        "decided_by": decision.decided_by,
        "plan": plan_out,
    }
    if not awaits_purchase_record(decision.decided_by):
        return PurchaseRecordOut(
            **base,
            status="NOT_REQUIRED",
            reason="자동 승인은 실매입 기록 없이 계획값으로 반영됩니다",
        )

    sim_run_id = approval.sim_run_id
    rows = (
        list_purchase_record_legs(
            sim_run_id=sim_run_id, request_id=request_id, decision_seq=decision.decision_seq
        )
        if sim_run_id is not None
        else []
    )
    if not rows or sim_run_id is None:
        reason = "실매입을 기록하면 반영됩니다"
        if plan is None and approval.plan_out is not None and approval.plan_out.reason:
            reason = f"선정안 약정이 서지 않았다: {approval.plan_out.reason}"
        return PurchaseRecordOut(**base, status="AWAITING_PURCHASE_RECORD", reason=reason)

    # ★ **기록 표에 단가 칸을 더하지 않는다** (2026-09-16 · DDL 없음). `quantity_kg` 와
    #   `amount_krw` 가 이미 있고, 입력이 단가라 저장된 금액이 **수량 × 단가**다 —
    #   그래서 `amount_krw ÷ quantity_kg` 가 적은 단가로 **정확히** 돌아온다. 칸을 더하면
    #   같은 사실이 두 칸에 앉아 둘이 갈리는 날이 온다.
    record = PurchaseRecordValuesOut(
        grade=str(rows[0]["grade"]),
        recorded_by=str(rows[0]["recorded_by"]),
        recorded_at=rows[0]["recorded_at"],
        legs=[
            PurchaseRecordLegOut(
                seq=int(row["leg_seq"]),
                qty_kg=float(row["quantity_kg"]),
                unit_price_krw=recorded_unit_price(row),
                amount_krw=float(row["amount_krw"]),
                purchase_date=row["purchase_date"],
                arrival_date=row["arrival_date"],
            )
            for row in rows
        ],
    )
    prefix = purchase_id_prefix_for(request_id, decision.decision_seq)
    applied = any(one.startswith(prefix) for one in ledger_purchase_ids(sim_run_id=sim_run_id))
    if applied:
        return PurchaseRecordOut(**base, status="APPLIED", record=record)
    return PurchaseRecordOut(
        **base,
        status="NOT_APPLIED",
        reason=(
            "기록은 남았습니다 · 입고 처리 중입니다"
            " — 다음 개장 때 매입 원장에 반영됩니다"
        ),
        record=record,
    )
