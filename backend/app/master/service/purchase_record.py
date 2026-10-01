"""purchase_record.py — **실매입 기록**이 전이를 세운다 (설계 260915 안 A §4-3 · §4-6).

```text
[전]  판단 → 승인 ──(같은 커밋)──▶ 전이   ← 안의 계획값
[후]  판단 → 승인(선정만 · 전이 보류) → 실매입 기록 ──▶ 전이   ← 실매입 값
```

★★ **사람이 승인하면 선정만 적힌다** (`decision_service.record_decision`). 사람이
  실제로 산 값을 여기서 적는 순간, 그 값으로 기존 전이(매입 원장 · 매입채무 · 입고
  일정)가 돈다. 그 뒤 입고 → 재고 lot → 판매 · 재무 현금은 **지금 코드 그대로** 이 값을
  따라간다.

🔴 **약정을 새로 짓지 않는다.** 선정안으로 조립하던 그 경로
   (`decision_service.current_approval` → `_commitment_parts` → `build_commitment`)가
   만든 약정의 **사본에 값만 덮는다** (`commitment.with_purchase_record`).

🔴 **기록값이 선정안과 다르면 승인 때와 같은 재검증을 다시 지난다** (§4-6 ①). 기록이
   재무 Cap · 현금흐름 검증을 우회하는 문이 되면 안 된다. 통과 못 하면 기록도 전이도 없다.

🔴 **전이 코드를 안 고친다.** `transition.apply_approval` 을 그대로 부른다. 다만
   **기록 행 적재와 묶지 않는다** — 기록이 제 트랜잭션으로 먼저 커밋되고, 전이는 그
   뒤에 별도 커넥션으로 돈다 (`record_purchase` 독스트링 · 2026-09-16).

⚠️ **자동 승인(`AUTO-BACKFILL`)은 이 경로를 안 탄다** — 지금처럼 승인 즉시 계획값으로
  전이한다 (§2).

★ 2026-09-30 재구성 BL-018: `master/purchase_record.py` 에서 옮겼다. 역할이 다른 부분은 갈랐다 —
  `domain/purchase_record.py`; `readmodel/purchase_record.py`. 무엇이 어디로 갔는지는 설계서 대응표
  `master/` 절.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from psycopg import errors as pg_errors

from app.contracts.commitment import ApprovedCommitment, CommitmentNotBuildable
from app.core import db as core_db
from app.master.domain.commitment import RecordedLeg
from app.master.domain.decision import awaits_purchase_record
from app.master.domain.purchase_record import (
    BEFORE_APPROVAL_MESSAGE,
    CLOSED_DUE_DATE_MESSAGE,
    SAME_DAY_DUE_DATE_MESSAGE,
    check_recordable_values,
    check_single_grade,
    differs_from_plan,
    recorded_scenario,
)
from app.master.domain.revalidation import find_scenario
from app.master.readmodel.approvals import commitment_with_record, list_purchase_record_legs
from app.master.readmodel.purchase_record import approval_to_record, last_closed_date
from app.master.repository.purchase_records import insert_purchase_record_legs
from app.master.schemas.approval import CurrentApproval
from app.master.schemas.decision import DecisionRejected
from app.master.schemas.purchase_record import PurchaseRecordIn
from app.master.schemas.transition import TransitionOut
from app.master.service.decision import revalidate_recorded
from app.master.service.transition import apply_approval

#: 승인 때 재검증을 통과로 보는 결과. `CONDITIONAL` · `FAILED` · `ERROR` 는 통과가 아니다
#: (`decision.RevalidationOutcome` 의 표).
_PASSED = "PASSED"


def record_purchase(
    request_id: str,
    body: PurchaseRecordIn,
    *,
    borrow: core_db.Borrow | None = None,
    apply_fn: Callable[..., TransitionOut] = apply_approval,
) -> TransitionOut:
    """실매입을 적고 **그 값으로 전이를 세운다.**

    ```text
    검증  승인(APPROVE) 존재 · 사람 승인 · 아직 기록 없음 · 회차 집합 == 선정안 회차 집합
          선검사  안의 등급이 하나 · 첫 회차 매입일 == 승인 실행 as_of
                  · 수량이 정수 · 금액 ÷ 수량(= 단가)이 정수
          회차마다 매입일 >= 승인 실행 as_of · 지급기일 > 마지막 재무 일마감일 (같은 날은 예외 하나)
    재검증 기록값이 선정안과 하나라도 다르면 · 기록값 안 사본으로 · PASSED 가 아니면 멈춘다
    ①    master_purchase_records 에 회차 행 — **제 커넥션 · 제 커밋**
    ②    선정안 약정 사본에 기록값을 덮는다
    ③    apply_approval(사본) — ① 이 커밋된 **뒤** · **별도 커넥션**
    ```

    🔴 **기록과 전이는 두 트랜잭션이다** (2026-09-16 · `#729`). 전에는 한 커넥션으로
       묶었는데, `apply_approval` 은 적재하다 터지면 그 커넥션을 `rollback` 하므로
       **전이가 실패하면 기록 행까지 사라졌다.**

       ★ 그런데 **전이 실패는 예외가 아니라 정상 경로다.** 전이는 언제나 하루 앞
         (도착일)의 물류 runtime fixture 행을 보는데 그 행은 다음 개장에 열린다 —
         그래서 **승인 당일의 전이는 언제나 `FAILED` 이고 다음 날 「미적용 전이
         재시도」가 세운다** (`pending_transition.py` 머리말).

       ```text
       실측  dev@983c85b · SIM-CHECK-HOLIDAY-0916 · 2026-04-13 무
         POST .../purchase-record → 201 {"status":"FAILED","reason":"전이 적재 실패:
           갱신할 물류 runtime fixture 행이 없다 (as_of=2026-04-14)..."}
         SELECT ... FROM master_purchase_records WHERE sim_run_id='SIM-CHECK-HOLIDAY-0916'
           → 0행                      🔴 사람이 적은 사실이 지워졌다
         다음 날 걷기: 미적용 전이 재시도 NOTHING_DUE — 기록이 없어 재시도 대상에서도
           빠지고(`#718`) 매입은 영영 안 선다
       ```

       ★ **기록은 사람이 진술한 사실이고 전이는 그 귀결이다.** 장부가 아직 준비되지
         않았다는 이유로 사람의 진술이 지워지면 안 된다. 그 상태는 설계에 이미 있다 —
         `PurchaseRecordStatus.NOT_APPLIED`("기록 있음 · 아직 원장에 없다 → 다음 개장
         뒤 재시도가 기록값으로", `decision.py`).

    ★ 그래서 **전이가 무엇을 돌려주든 기록은 남는다** — 커넥션 앞에서 돌아서든
      (`NOT_APPLIED`) 적재하다 롤백하든(`FAILED`). 사람은 다시 보내지 않고, 다음
      개장 뒤 재시도가 기록값으로 세운다.
    🔴 **기록 삽입 자체가 실패하면 전이를 안 부른다** — 없는 기록의 귀결은 없다.
      이미 기록된 승인이면 `UniqueViolation` 이 409 로 접힌다 (커밋이 앞당겨져도 같다).

    :raises LookupError: 승인이 없다 (404).
    :raises DecisionRejected: 지금 상태에서 받을 수 없다(409) · 기록이 선정안과 안 맞다 ·
        마감된 날짜다 · 재검증을 통과하지 못했다(422).
    """
    approval = approval_to_record(request_id)
    decision = approval.decision
    if body.decision_seq != decision.decision_seq:
        raise DecisionRejected(
            f"회차 {body.decision_seq} 는 현재 승인이 아니다"
            f" (현재 승인 회차 {decision.decision_seq}).",
            conflict=True,
        )
    if not awaits_purchase_record(decision.decided_by):
        raise DecisionRejected(
            "자동 승인은 실매입 기록 대상이 아니다 — 승인 즉시 계획값으로 반영된다.",
            conflict=True,
        )
    sim_run_id = approval.sim_run_id
    if sim_run_id is None:
        raise DecisionRejected(
            "원 실행의 sim_run_id 를 못 읽어 어느 장부에 반영할지 정할 수 없다.",
            conflict=True,
        )
    plan = approval.plan
    if plan is None:
        reason = approval.plan_out.reason if approval.plan_out is not None else None
        raise DecisionRejected(
            f"선정안 약정이 서지 않아 기록할 회차가 없다: {reason or '사유 없음'}",
            conflict=True,
        )
    if list_purchase_record_legs(
        sim_run_id=sim_run_id, request_id=request_id, decision_seq=decision.decision_seq
    ):
        raise DecisionRejected(
            f"이 승인(회차 {decision.decision_seq})에는 이미 실매입이 기록됐다 — 고쳐 쓰지 않는다.",
            conflict=True,
        )

    legs = tuple(
        RecordedLeg(
            seq=leg.seq,
            qty_kg=leg.qty_kg,
            amount_krw=leg.amount_krw,
            purchase_date=leg.purchase_date,
            arrival_date=leg.arrival_date,
        )
        for leg in body.legs
    )
    # ★ **선검사가 재검증보다 앞이다** — 재검증이 계약에서 떨어지면 사람에게는
    #   「재검증 통과 못 함」만 남는다 (2026-09-16).
    check_single_grade(plan)
    check_recordable_values(approval, legs)
    grade = body.grade.strip()
    try:
        recorded = commitment_with_record(approval, legs, grade)
    except CommitmentNotBuildable as exc:
        raise DecisionRejected(str(exc)) from exc
    # ★ 경계는 **덮은 약정**으로 잰다 — 지급기일의 주인이 약정 덮기(`with_purchase_record`)다.
    _check_purchase_dates(approval, recorded, sim_run_id=sim_run_id)

    if differs_from_plan(plan, legs, grade):
        _revalidate_or_reject(approval, legs, grade)

    open_connection = core_db.connection if borrow is None else borrow
    # ① 기록을 **먼저 · 제 트랜잭션으로** 커밋한다. 이 커넥션은 전이에 넘기지 않는다 —
    #    넘기면 전이의 rollback 이 기록까지 되감는다 (위 독스트링 실측).
    with open_connection() as conn:
        try:
            insert_purchase_record_legs(
                conn,
                sim_run_id=sim_run_id,
                request_id=request_id,
                decision_seq=decision.decision_seq,
                grade=grade,
                recorded_by=body.recorded_by.strip(),
                legs=legs,
            )
            conn.commit()
        except pg_errors.UniqueViolation as exc:
            conn.rollback()
            raise DecisionRejected(
                f"이 승인(회차 {decision.decision_seq})에는 이미 실매입이 기록됐다.",
                conflict=True,
            ) from exc
        except Exception:
            # 🔴 기록이 안 앉았으면 전이를 안 부른다 — 없는 기록의 귀결은 없다.
            conn.rollback()
            raise

    # ③ 전이는 **커밋된 기록 뒤에** 제 커넥션으로 돈다(①의 연결은 이미 돌려줬다). 실패해도
    #    기록은 남고, 다음 개장 뒤 「미적용 전이 재시도」가 기록값으로 세운다.
    return apply_fn(recorded, sim_run_id=sim_run_id, borrow=borrow)


def _check_purchase_dates(
    approval: CurrentApproval, recorded: ApprovedCommitment, *, sim_run_id: str
) -> None:
    """매입일 경계 (§4-6 ② · 2026-09-16 변경). **회차마다 두 줄.**

    ```text
    매입일   >= 승인 실행 as_of
    지급기일 >  그 sim_run_id 의 마지막 재무 일마감일   (지급기일 = 매입일 + N5)
    지급기일 == 마지막 마감일   승인 기준일 == 매입일 == 마지막 마감일 일 때만 받는다
    ```

    ★ **매입일이 아니라 지급기일로 마감일과 견준다** (마스터 확정 · 재무 요청 취지).
      걷기가 D 를 마감한 뒤 사람이 D 매입을 승인 · 기록하는 것이 정상 순서다. 막아야
      하는 것은 *"이미 지난 지급기일의 채무가 새로 생기는 것"* 이다 — 그 채무는 마감된
      날의 지급에 한 번도 안 잡힌다.

    ★ **D 마감 뒤 입력되는 동일일 실매입만 예외다** (2026-09-16 재무 합의). 재무 마감
      (`finance/closing._recognize_due_payables`)은 `issued_date <= as_of AND due_date <= as_of`
      이고 아직 마감 사건이 없는 채무를 **다음 마감에서 한 번** 반영한다. 그래서 D 에
      승인한 D 매입(D+0 지급)은 현금이 D+1 마감에서 한 번 잡힌다. 🔴 그 밖의 동일일
      (과거 승인 · N5 가 있어 지급기일이 마침 마감일인 경우)은 거부한다.

    ★ **D 마감 숫자는 흔들리지 않는다.** 전이(`finance/service/transition.py`)는 as_of 날 상태를
      읽기만 하고 `as_of + 1` 상태에 쓴다 (`master/transition._target_state_date`).

    ★ **지급기일의 주인은 덮은 약정이다** (`ArrivalLeg.payment_due_date`). 여기서 N5 를
      다시 더하지 않는다.

    🔴 **마감이 있는데 지급기일을 모르면 거부한다.** 못 잰 것을 통과로 두지 않는다.
    """
    as_of = approval.as_of
    closed = last_closed_date(sim_run_id=sim_run_id)
    for leg in recorded.arrival_schedule:
        if as_of is not None and leg.purchase_date < as_of:
            raise DecisionRejected(
                f"{BEFORE_APPROVAL_MESSAGE}"
                f" ({leg.seq}회차 매입일 {leg.purchase_date} · 승인 기준일 {as_of})"
            )
        if closed is None:
            continue
        due = leg.payment_due_date
        if due is None:
            raise DecisionRejected(
                "지급기일을 계산할 수 없어 마감 여부를 확인하지 못했습니다"
                f" — 재무 지급 일수를 확인해 주세요 ({leg.seq}회차 · 마지막 마감일 {closed})"
            )
        if due < closed:
            raise DecisionRejected(
                f"{CLOSED_DUE_DATE_MESSAGE} ({leg.seq}회차 지급기일 {due} · 마지막 마감일 {closed})"
            )
        if due == closed and not (as_of == leg.purchase_date == closed):
            raise DecisionRejected(
                f"{SAME_DAY_DUE_DATE_MESSAGE}"
                f" ({leg.seq}회차 승인 기준일 {as_of} · 매입일 {leg.purchase_date}"
                f" · 지급기일 {due} · 마지막 마감일 {closed})"
            )


def _revalidate_or_reject(
    approval: CurrentApproval, legs: Sequence[RecordedLeg], grade: str
) -> None:
    """기록값 안 사본으로 재검증한다. **`PASSED` 가 아니면 422** — 저장도 전이도 없다."""
    label = approval.decision.scenario_label or ""
    scenario = find_scenario(approval.response_payload, label)
    if scenario is None:
        raise DecisionRejected(
            f"승인한 안 '{label}' 을 원 실행에서 유일하게 찾지 못해 기록값을 재검증할 수 없다.",
            conflict=True,
        )
    copied = recorded_scenario(
        scenario,
        legs=legs,
        grade=grade,
        purchase_payment_days=approval.purchase_payment_days,
    )
    result = revalidate_recorded(approval, copied)
    if result.outcome != _PASSED:
        raise DecisionRejected(
            f"기록값으로 다시 검증했더니 통과하지 못해 기록하지 않았습니다"
            f" ({result.outcome}): {result.reason or '사유 없음'}"
        )
