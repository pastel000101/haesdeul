"""검수 판정 어휘 · 결과 · 실패 종류.

순서는 `service/inspections.py`, 판정 검증은 `domain/inspections.py`, SQL 은
`repository/inspections.py` 에 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, get_args

from app.logistics.schemas.receipts import ReceiptStatus

#: `ck_inbound_inspections_verdict` 어휘 그대로다. 새 판정을 만들지 않는다.
InspectionVerdict = Literal["PASS", "HOLD", "REJECT"]

VERDICTS: frozenset[str] = frozenset(get_args(InspectionVerdict))

#: 창고에서 사람이 확인한 것이 아니다. `ck_inbound_inspections_fact_source` 는
#: `HUMAN_RECORDED · SCENARIO_SIMULATED` 둘뿐이고, 자동 경로에 앞엣것을 쓰면 거짓이다.
INSPECTION_FACT_SOURCE = "SCENARIO_SIMULATED"


class InspectionError(RuntimeError):
    """이 모듈이 내는 실패의 조상 (`receipts.ReceiptLookupError` 와 같은 결)."""


class InvalidInspectionOutcome(InspectionError, ValueError):
    """검수 결과가 DB 계약을 어긴다. 쓰기 전에 막는다.

    DB CHECK 가 어차피 막지만, 거기까지 가면 트랜잭션이 aborted 된다 —
    바깥이 롤백할 수밖에 없어 멀쩡한 다른 작업까지 잃는다 (`ledger.py` 가 업무
    검증을 DML 앞에 두는 것과 같은 이유).
    """


class InspectionIntegrityError(InspectionError, ValueError):
    """검수와 Receipt 가 서로를 배반한다. 무결성 위반이다.

    조용히 고치지 않는다. 한 Receipt 에 검수가 둘이거나, Receipt 가
    `INSPECTED` 인데 검수 행이 없거나, 이미 마감된 Receipt 의 수량이 검수와 다른
    경우다 — 어느 쪽이 진짜인지 여기서 고를 근거가 없다.
    """


class InspectionConflict(InspectionError, ValueError):
    """같은 Receipt 에 다른 사실의 검수가 이미 있다.

    덮어쓰지 않는다. 기존을 남기면 이번 결과가 조용히 사라지고, 갈아 끼우면
    앞 결과가 사라진다 — 둘 다 "에러 없이 틀리는" 쪽이다
    (`schemas/transition.InboundScheduleConflict` 와 같은 판단).
    """


@dataclass(frozen=True)
class InspectionOutcome:
    """검수 판정과 수량. 호출자가 주는 사실이다 — 여기서 만들지 않는다."""

    verdict: InspectionVerdict
    inspected_qty_kg: Decimal
    accepted_qty_kg: Decimal
    hold_qty_kg: Decimal
    reject_qty_kg: Decimal


@dataclass(frozen=True)
class InspectionRecord:
    """이미 적혀 있던 검수 한 건."""

    inspection_id: str
    outcome: InspectionOutcome


@dataclass(frozen=True)
class InspectionWriteResult:
    """`record_inspection` 의 결과.

    `applied=False` 는 "할 일이 없다" 가 아니다. 뜻은 하나 — "이 호출이 검수
    행을 새로 만들지 않았다." Lot · 원장 IN 은 아직 남아 있을 수 있다.
    """

    #: 이번 호출이 검수 행을 새로 만들었나.
    applied: bool
    inspection_id: str
    #: 이 호출이 끝난 시점의 Receipt 상태.
    receipt_status: ReceiptStatus
    #: 권위 있는 검수 사실 — 새로 썼으면 그 값, 이미 있었으면 DB 에 적힌 값.
    outcome: InspectionOutcome
