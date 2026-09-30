"""검수의 판정 — 검수 ID · 수량 항등식 · 같은 검수 사실 · Receipt 수량 대조 · 시각/검수자 검사.

★ 2026-09-30 재구성 BL-015: `logistics/inspections.py` 에서 옮겼다. DB 를 만지지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.logistics.schemas.inspections import VERDICTS, InspectionOutcome, InvalidInspectionOutcome


def inspection_id_for(*, receipt_id: str) -> str:
    """검수 행의 PK. **순수 계산이고 결정론이다.**

    ```text
    INSP-{receipt_id}
    → INSP-RCPT-SIM-BURNIN-202512-INB-H1-THRU-20260105-BAECHU-1-1
    ```

    ★ `receipt_id` 가 이미 실행(`sim_run_id`)과 입고(`inbound_id`) 정체성을 담고
      있어 그 위에 접두사만 얹으면 된다.

    🔴 난수 · 시계 · DB 시퀀스 · 매입 ID 유도를 쓰지 않는다. 같은 Receipt 는 몇 번을
       불러도 같은 값이어야 재시도가 멱등해진다.

    ⚠️ **저장소에 검수 ID 규약이 없었다 (실측: 씨앗 0건 · 코드 0건).** 이 규칙은
       물류가 새로 정한 것이고, `receipt_id_for` 와 같은 결로 맞췄다.
    """
    if not receipt_id or not receipt_id.strip():
        raise InvalidInspectionOutcome(
            f"inspection_id 를 지을 수 없다 — receipt_id 가 비었다: {receipt_id!r}"
        )
    return f"INSP-{receipt_id}"


def inspection_quantity(value: object, *, 칸: str) -> Decimal:
    """수량을 `Decimal` 로 좁힌다. **float 도 비유한값도 받지 않는다.**

    ★ `ledger._quantity` 와 같은 규율이다 — `NaN` · `Infinity` 는 DB CHECK 의
      부등식을 **조용히 통과할 수 있어** 항등식이 깨진 행이 남는다.
    """
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise InvalidInspectionOutcome(
            f"{칸} 은 Decimal 이어야 한다 (받은 것: {value!r} · {type(value).__name__})."
            " float 은 이진 오차를 수량에 남긴다."
        )
    if not value.is_finite():
        raise InvalidInspectionOutcome(f"{칸} 이 유한한 수가 아니다: {value!r}")
    return value


def validate_outcome(outcome: InspectionOutcome) -> None:
    """DB CHECK 와 **같은 규칙**을 쓰기 전에 건다. 순수 계산이다.

    ```text
    inspected > 0 · accepted/hold/reject >= 0
    accepted + hold + reject = inspected          ← 항등식
    PASS    hold = 0 · reject = 0
    HOLD    hold > 0
    REJECT  accepted = 0 · reject > 0
    ```

    🔴 **값을 고쳐 맞추지 않는다.** 합이 안 맞으면 어느 쪽이 맞는지 우리가 모른다.
    """
    if outcome.verdict not in VERDICTS:
        raise InvalidInspectionOutcome(
            f"검수 판정이 계약 어휘 밖이다: {outcome.verdict!r}. 허용: {sorted(VERDICTS)}."
        )

    inspected = inspection_quantity(outcome.inspected_qty_kg, 칸="inspected_qty_kg")
    accepted = inspection_quantity(outcome.accepted_qty_kg, 칸="accepted_qty_kg")
    hold = inspection_quantity(outcome.hold_qty_kg, 칸="hold_qty_kg")
    reject = inspection_quantity(outcome.reject_qty_kg, 칸="reject_qty_kg")

    if inspected <= 0:
        raise InvalidInspectionOutcome(f"검수량은 0보다 커야 한다 (받은 것: {inspected})")
    for 칸, 값 in (("accepted", accepted), ("hold", hold), ("reject", reject)):
        if 값 < 0:
            raise InvalidInspectionOutcome(f"{칸} 수량이 음수다: {값}")
    if accepted + hold + reject != inspected:
        raise InvalidInspectionOutcome(
            f"수량 항등식이 깨졌다: {accepted} + {hold} + {reject} != {inspected}."
            " 어느 쪽이 맞는지 여기서 고치지 않는다."
        )

    if outcome.verdict == "PASS" and (hold != 0 or reject != 0):
        raise InvalidInspectionOutcome(f"PASS 인데 보류 {hold} · 거부 {reject} 가 있다.")
    if outcome.verdict == "HOLD" and hold <= 0:
        raise InvalidInspectionOutcome("HOLD 인데 보류 수량이 0 이다.")
    if outcome.verdict == "REJECT" and (accepted != 0 or reject <= 0):
        raise InvalidInspectionOutcome(f"REJECT 인데 수용 {accepted} · 거부 {reject} 다.")


def same_inspection_facts(기존: InspectionOutcome, 이번: InspectionOutcome) -> bool:
    """두 검수 결과가 같은 사실인가.

    ★ `Decimal` 로 비교한다 — `numeric` 이라 `10` 과 `10.000000` 이 같은 수량인데
      문자열은 다르다. 그대로 비교하면 **정상 재실행이 Conflict 로 뒤집힌다**
      (`transition._같은_사실` 이 같은 함정을 피한다).
    """
    return (
        기존.verdict == 이번.verdict
        and 기존.inspected_qty_kg == 이번.inspected_qty_kg
        and 기존.accepted_qty_kg == 이번.accepted_qty_kg
        and 기존.hold_qty_kg == 이번.hold_qty_kg
        and 기존.reject_qty_kg == 이번.reject_qty_kg
    )


def receipt_quantities_match(receipt: Mapping[str, Any], outcome: InspectionOutcome) -> bool:
    """Receipt 에 옮겨진 수량이 검수와 일치하나. `None` 은 아직 안 옮긴 것이다."""
    return (
        receipt["accepted_qty_kg"] == outcome.accepted_qty_kg
        and receipt["hold_qty_kg"] == outcome.hold_qty_kg
        and receipt["rejected_qty_kg"] == outcome.reject_qty_kg
    )


def check_inspection_stamp(*, inspected_at: datetime, inspector: str) -> None:
    """검수 시각(tz 있는 datetime)과 검수자가 있는가. DB 를 만지지 않는다.

    ★ `record_inspection` 의 ① 검증을 떼어 낸 것이다 — 순서·문구 그대로.
    """
    if not isinstance(inspected_at, datetime):
        raise InvalidInspectionOutcome(
            f"inspected_at 은 datetime 이어야 한다 (받은 것: {inspected_at!r})."
        )
    if inspected_at.tzinfo is None or inspected_at.utcoffset() is None:
        # 🔴 naive 를 TIMESTAMPTZ 에 넣으면 세션 TimeZone 에 따라 **뜻이 달라진다.**
        raise InvalidInspectionOutcome(
            f"inspected_at 에 시간대가 없다: {inspected_at!r}."
            " TIMESTAMPTZ 라 tz 없는 값은 세션 설정에 따라 다른 시각이 된다."
        )
    if not inspector or not inspector.strip():
        raise InvalidInspectionOutcome(
            "inspector 가 비었다. NOT NULL 이고 물류가 지어내지 않는다 —"
            " 저장소에 시스템 행위자 규약이 없어 호출자가 정할 값이다."
        )
