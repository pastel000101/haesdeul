"""inspections.py — 도착한 Receipt 를 **검수 결과로 마감한다** (3-B4-H).

```text
ARRIVED Receipt
   → InspectionOutcome (호출자가 준다)
   → inbound_inspections INSERT
   → Receipt 수량 + receipt_status = INSPECTED
```

🔴 **검수 결과를 지어내지 않는다.** 저장소 어디에도 *"자동 시뮬레이션에서 몇 %가
   PASS 인가"* 를 정한 규칙이 없다 (Persona · 정책 · 스키마 · 코드 전부 확인).
   없는 규칙을 여기서 만들면 그 비율이 곧 업무 사실이 되어 원가·폐기 판단으로
   흘러간다. 그래서 판정과 수량은 **호출자가 주는 입력**이고, 이 파일은 그것이
   DB 계약을 어기지 않는지만 본다.

   ⚠️ 시나리오 생성기가 붙는 날 **이 파일을 고칠 필요가 없다** — 결과를 만들어
      넘기기만 하면 된다.

🔴 **`inspector` 도 지어내지 않는다.** 저장소에 시스템 행위자 상수(`SYSTEM_*` 등)
   규약이 없다. `inspector` 는 NOT NULL 이지만 **호출자가 준다** — 없는 사람 이름을
   만드는 것보다 그 값을 정할 자리(시나리오 생성기 · 웹 Form)에 맡기는 것이 맞다.

🔴 **`inspected_at` 에 시계를 읽지 않는다.** `datetime.now()` 를 부르지 않고
   호출자가 준 값을 쓴다 — 같은 시뮬레이션을 다시 돌리면 같은 값이 나와야 한다.
   `arrived_at` 이 `DATE` 인 것과 달리 이 칸은 `TIMESTAMPTZ` 라, **tz 를 단 값만**
   받는다 (naive 를 넣으면 세션 TimeZone 에 따라 뜻이 달라진다).

★ **`receipts.py` 와 나눈 이유.** 저쪽은 *"Receipt 행이 있나 · 만든다"* 이고
  이쪽은 *"검수 사실을 적고 Receipt 를 마감한다"* 다. 잠금은 **저쪽 것을 그대로
  쓴다** — 세 번째 잠금을 만들지 않는다.

🔴 **스키마 실측 (2026-09-05 · 저장소 DDL 과 실 DB 카탈로그 일치).**

  ```text
  inbound_inspections
    PK        inspection_id 단독
    UNIQUE    🔴 receipt_id 에 **없다** — 한 Receipt 에 검수 여러 건이 물리적으로 가능
    FK        receipt_id → inbound_receipts
    CHECK     verdict IN (PASS, HOLD, REJECT)
    CHECK     inspected > 0 · 나머지 >= 0 · accepted + hold + reject = inspected
    CHECK     PASS→hold=0,reject=0 / HOLD→hold>0 / REJECT→accepted=0,reject>0
  ```

  ⚠️ **UNIQUE 가 없다고 스키마를 지금 고치지 않는다.** 대신 조회에서 0 · 1 · 2+ 를
     갈라 방어한다 (`repository` 의 활성 fixture, `receipts` 의 Receipt 중복과 같은
     규율). 첫 행을 집지 않는다.

  ⚠️ **칸 이름이 두 표에서 다르다.** 검수는 `reject_qty_kg`, Receipt 는
     `rejected_qty_kg` 다 — 실측이고, 옮길 때 이 차이를 잊으면 조용히 어긋난다.

  ★ `updated_at` 갱신 **트리거가 없다** (실측). 그래서 Receipt UPDATE 가
    `updated_at = now()` 를 직접 적는다 — DB 의 `DEFAULT now()` 는 INSERT 에만 걸린다.
    ⚠️ 이 `now()` 는 **DB 의 기록 시각**이지 업무 사실이 아니다. 업무 시각인
       `inspected_at` 은 위에서 말한 대로 호출자가 준다.

⚠️ **`inbound_inspection_checks` 를 쓰지 않는다.** 그 표의 주석이 *"사람이 웹 Form
   으로 넣는다"* 이고, 항목이 필수라는 정책이 어디에도 없다. `MOLD=false` 를 채우면
   **하지 않은 관찰을 했다고 적는 것**이 된다.

★ 2026-09-30 재구성 BL-015: `logistics/inspections.py` 을 계층별로 나눴다. 이 파일에는 검수 기록의
  **순서**가 남았다. 판정은
  `domain/inspections.py`, SQL 은 `repository/inspections.py`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.logistics.domain.inspections import (
    check_inspection_stamp,
    inspection_id_for,
    receipt_quantities_match,
    same_inspection_facts,
    validate_outcome,
)
from app.logistics.repository.inspections import (
    find_inspection,
    insert_inspection,
    mark_receipt_inspected,
    select_receipt_for_inspection,
)
from app.logistics.repository.locks import lock_arrival_writes
from app.logistics.schemas.inspections import (
    InspectionConflict,
    InspectionIntegrityError,
    InspectionOutcome,
    InspectionWriteResult,
)
from app.logistics.schemas.vocabulary import RECEIPT_BEFORE_INSPECTION, RECEIPT_INSPECTION_SETTLED


def record_inspection(
    conn: Any,
    *,
    receipt_id: str,
    inspected_at: datetime,
    inspector: str,
    outcome: InspectionOutcome,
) -> InspectionWriteResult:
    """검수 결과를 적고 Receipt 를 `INSPECTED` 로 마감한다. **멱등하다.**

    ```text
    ① 결과·인자 검증                  DB 를 안 만진다
    ② 도착 쓰기 전역 advisory lock     receipts 의 그 잠금을 그대로 쓴다
    ③ Receipt 상태 읽기 (PK)
    ④ 기존 검수 읽기 (0 · 1 · 2+)
    ⑤ 판단 → 필요할 때만 INSERT · UPDATE
    ```

    **상태기계 (MVP):**

    ```text
    상태                  검수 0행              검수 1행 (같은 사실)   검수 1행 (다른 사실)
    ARRIVED · INSPECTING  INSERT + INSPECTED    Receipt 만 맞춘다      InspectionConflict
    INSPECTED             🔴 무결성 오류         그대로 (applied=False) InspectionConflict
    PUTAWAY_DONE · CLOSED 🔴 무결성 오류         그대로 (applied=False) InspectionConflict
    어느 상태든 2행 이상   InspectionIntegrityError
    ```

    🔴 **`INSPECTED` 인데 검수 행이 0 이면 조용히 다시 만들지 않는다.** 그 상태는
       *"검수를 이미 했다"* 는 주장이고, 사실이 없는데 새로 적으면 **사라진 결과가
       있었다는 것조차 안 남는다.** 복구 경로는 스키마에도 코드에도 없다.

    ★ **`ARRIVED` 인데 검수 행이 이미 있으면 — 사실이 같을 때만 Receipt 를 맞춘다.**
      그때 쓰는 값은 정상 경로가 썼을 값과 **글자 그대로 같아서** 새 정보를 만들지
      않는다. 사실이 다르면 고치지 않고 멈춘다. 이것이 이 자리에서 방어할 수 있는
      가장 작은 행동이다.

    ⚠️ **이미 마감된 Receipt(`INSPECTED` 이상)의 상태는 되돌리지도 앞당기지도
       않는다.** 그 수량이 검수와 다르면 그것은 무결성 오류다 — 나중 단계가 이미
       그 값으로 움직였을 수 있어 덮어쓰면 안 된다.

    🔴 **커밋도 롤백도 하지 않고 커넥션을 새로 열지 않는다.** 잠금도 트랜잭션
       수명이라 호출자의 커밋/롤백과 함께 풀린다.

    :param inspected_at: 검수 시각. **호출자가 준다** — 시계를 읽지 않는다.
        `TIMESTAMPTZ` 라 tz 를 단 값만 받는다.
    :param inspector: 검수자. **호출자가 준다** — 없는 사람을 지어내지 않는다.
    """
    # ── ① 검증 — DB 를 만나기 전에 끝낸다 ──────────────────────────────
    inspection_id = inspection_id_for(receipt_id=receipt_id)
    validate_outcome(outcome)
    check_inspection_stamp(inspected_at=inspected_at, inspector=inspector)

    # ── ② 잠금이 먼저다 (도착 쓰기와 같은 전역 키) ─────────────────────
    lock_arrival_writes(conn)

    # ── ③④ 잠금 안에서 두 사실을 읽는다 ───────────────────────────────
    receipt = select_receipt_for_inspection(conn, receipt_id=receipt_id)
    상태 = receipt["receipt_status"]
    기존 = find_inspection(conn, receipt_id=receipt_id)

    # ── ⑤ 판단 ────────────────────────────────────────────────────────
    if 기존 is not None:
        if not same_inspection_facts(기존.outcome, outcome):
            raise InspectionConflict(
                f"같은 Receipt 에 다른 사실의 검수가 이미 있다 (receipt_id={receipt_id!r})."
                f" 기존={기존.outcome!r} 이번={outcome!r}."
                " 덮지도 버리지도 않는다 — 어느 쪽이 진짜인지 여기서 고를 근거가 없다."
            )
        if 상태 in RECEIPT_BEFORE_INSPECTION:
            # ★ 반쪽 상태를 맞춘다. 쓰는 값이 정상 경로와 **같아서** 안전하다.
            mark_receipt_inspected(conn, receipt_id=receipt_id, outcome=기존.outcome)
            상태 = "INSPECTED"
        elif not receipt_quantities_match(receipt, 기존.outcome):
            raise InspectionIntegrityError(
                f"이미 마감된 Receipt 의 수량이 검수와 다르다"
                f" (receipt_id={receipt_id!r}, receipt_status={상태!r})."
                " 뒤 단계가 이미 그 값으로 움직였을 수 있어 덮어쓰지 않는다."
            )
        return InspectionWriteResult(
            applied=False,
            inspection_id=기존.inspection_id,
            receipt_status=상태,
            outcome=기존.outcome,
        )

    if 상태 in RECEIPT_INSPECTION_SETTLED:
        raise InspectionIntegrityError(
            f"Receipt 는 {상태!r} 인데 검수 행이 없다 (receipt_id={receipt_id!r})."
            " 검수를 이미 했다는 상태이므로 여기서 새로 적지 않는다 —"
            " 사라진 결과가 있었다는 사실조차 안 남는다."
        )
    if 상태 not in RECEIPT_BEFORE_INSPECTION:
        raise InspectionIntegrityError(
            f"검수를 적을 수 없는 Receipt 상태다: {상태!r} (receipt_id={receipt_id!r})."
        )

    insert_inspection(
        conn,
        inspection_id=inspection_id,
        receipt_id=receipt_id,
        inspected_at=inspected_at,
        inspector=inspector,
        outcome=outcome,
    )
    mark_receipt_inspected(conn, receipt_id=receipt_id, outcome=outcome)

    return InspectionWriteResult(
        applied=True,
        inspection_id=inspection_id,
        receipt_status="INSPECTED",
        outcome=outcome,
    )
