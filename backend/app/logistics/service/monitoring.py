"""문제 탐지 → `logistics_exceptions` open · touch · resolve 의 순서.

탐지기는 `domain/monitoring.py`, 관측은 `readmodel/observation.py`, SQL 은
`repository/exceptions.py` 다. commit 은 마스터(`master/service/inspection.py`)가 한다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from app.logistics.domain.monitoring import (
    COMMITTED,
    DETECTORS,
    ESCALATED_FRESHNESS_EXPIRED,
    LOT_EMPTY,
    REDETECT,
)
from app.logistics.readmodel.observation import observe
from app.logistics.repository.exceptions import (
    exception_id_for,
    open_exception,
    previous_exception_id_for,
    resolve_exception,
    touch_exception,
)
from app.logistics.schemas.monitoring import (
    FRESHNESS_PRESSURE,
    DetectedCondition,
    DetectOut,
    DetectPhase,
    ExceptionRow,
    WarehouseObservation,
)

# ---------------------------------------------------------------------------
# 탐지 한 번 — 표에 적는다
# ---------------------------------------------------------------------------


def detect_logistics_exceptions(
    conn: Any,
    *,
    sim_run_id: str,
    as_of: date,
    phase: DetectPhase,
    observe_fn: Callable[..., WarehouseObservation] = observe,
) -> DetectOut:
    """물류 점검 한 칸. 커밋도 롤백도 하지 않는다 — 트랜잭션 주인은 마스터다.

    ```text
    AFTER_INBOUND    입고로 점유가 뛴 직후 · 하루 경과 반영 → 열거나 갱신한다
    AFTER_OUTBOUND   그날 출고·할당·폐기가 끝난 뒤        → 열고 갱신하고 닫는다
    ```

    닫는 자리가 출고 뒤 하나인 이유: 그날 조건을 없앤 사건(예약·출고 OUT·폐기)이 다 끝난
    뒤에 닫아야 "N일째" 가 정확하다. 입고 직후에 닫으면 그날 나갈 재고를 보기도 전에
    «해결됐다» 고 적게 된다.

    :raises Exception: 그대로 올린다. 하루를 계속 살리는 것은 `master/service/inspection.py`
        의 일이고, 여기서 삼키면 반쯤 쓴 트랜잭션이 커밋된다.
    """
    observation = observe_fn(conn, sim_run_id=sim_run_id, as_of=as_of)
    outcomes = [detector(observation) for detector in DETECTORS]

    conditions: dict[tuple[str, str, str], DetectedCondition] = {}
    for outcome in outcomes:
        for condition in outcome.conditions:
            conditions[condition.dedupe_key] = condition
    ran_codes = {outcome.code for outcome in outcomes if outcome.ran}

    live_rows = {row.dedupe_key: row for row in observation.open_exceptions}
    opened: list[str] = []
    updated: list[str] = []
    resolved: list[str] = []

    for key, condition in conditions.items():
        existing = live_rows.get(key)
        if existing is not None:
            touch_exception(
                conn,
                exception_id=existing.exception_id,
                severity=condition.severity,
                evidence=condition.evidence,
                last_detected_as_of=as_of,
                observed_as_of=condition.observed_as_of,
            )
            updated.append(existing.exception_id)
            continue
        opened.append(_open(conn, sim_run_id=sim_run_id, as_of=as_of, condition=condition))

    if phase == "AFTER_OUTBOUND":
        for key, row in live_rows.items():
            if key in conditions or row.code not in ran_codes:
                # 못 잰 코드는 닫지 않는다. 기준이 없어 안 본 것을 "해결됐다" 로 적으면 그
                # 문제는 아무도 다시 못 찾는다.
                continue
            reason, note = _resolution(row, observation)
            resolve_exception(
                conn, exception_id=row.exception_id, as_of=as_of, resolved_by=reason, note=note
            )
            resolved.append(row.exception_id)

    skipped = tuple(f"{outcome.code}:{outcome.skipped}" for outcome in outcomes if not outcome.ran)
    touched = bool(opened or updated or resolved)
    return DetectOut(
        as_of=as_of,
        phase=phase,
        status="RAN" if touched else "NOTHING_DUE",
        opened=tuple(opened),
        updated=tuple(updated),
        resolved=tuple(resolved),
        reason=(
            f"연 것 {len(opened)} · 갱신 {len(updated)} · 닫은 것 {len(resolved)}"
            if touched
            else f"확인했고 손댈 것이 없었다 (Lot {len(observation.lots)})"
        ),
        uncertainties=tuple(dict.fromkeys([*observation.uncertainties, *skipped])),
    )


def _open(conn: Any, *, sim_run_id: str, as_of: date, condition: DetectedCondition) -> str:
    """새 문제 한 줄. 재발이면 이전 행을 가리킨다 (재오픈하지 않는다)."""
    previous_id = previous_exception_id_for(
        conn,
        sim_run_id=sim_run_id,
        code=condition.code,
        subject_type=condition.subject_type,
        subject_id=condition.subject_id,
    )
    exception_id = exception_id_for(
        conn,
        sim_run_id=sim_run_id,
        code=condition.code,
        subject_id=condition.subject_id,
        opened_as_of=as_of,
    )
    open_exception(
        conn,
        row=ExceptionRow(
            exception_id=exception_id,
            sim_run_id=sim_run_id,
            code=condition.code,
            subject_type=condition.subject_type,
            subject_id=condition.subject_id,
            severity=condition.severity,
            status="OPEN",
            opened_as_of=as_of,
            last_detected_as_of=as_of,
            observed_as_of=condition.observed_as_of,
            evidence=condition.evidence,
            detector_version=condition.detector_version,
            previous_exception_id=previous_id,
            note=condition.note or None,
        ),
    )
    return exception_id


def _resolution(row: ExceptionRow, observation: WarehouseObservation) -> tuple[str, str | None]:
    """무엇이 닫았나. 원장 이동 ID 를 지어내지 않는다 — 갈래만 적는다.

    ```text
    LOT_EMPTY                     그 Lot 이 관측에서 사라졌다 (출고 · 폐기로 잔량 0)
    ESCALATED:FRESHNESS_EXPIRED   잔량은 남았는데 신선도가 다했다 (§7.1 E)
    COMMITTED                     할당이 잔량을 다 덮었다 — «위험 관리 상태» (§7.1 C)
    REDETECT                      그 밖 (용량 회복 등 · 조건이 그냥 거짓이 됐다)
    ```

    `ESCALATED:` 가 후속 Exception 을 만들지 않는다. `FRESHNESS_EXPIRED` 탐지기는
    `DETECTORS` 에 없다 — 넘어갔다는 사실만 남기고, 잔량이 남은 만료 재고를 실제로 다루는
    것은 자동 유지보수(`service/maintenance`)와 사람의 폐기 확정이다.
    """
    if row.code != FRESHNESS_PRESSURE:
        return REDETECT, None
    lot = next((one for one in observation.lots if one.lot_id == row.subject_id), None)
    if lot is None:
        return LOT_EMPTY, "관측에서 사라졌다 — 잔량 0"
    remaining = lot.remaining_freshness_days
    if remaining is not None and remaining <= 0:
        return (
            ESCALATED_FRESHNESS_EXPIRED,
            f"잔여 {remaining}일 · 잔량 {lot.remaining_qty_kg}kg 남음",
        )
    if lot.uncommitted_kg is not None and lot.uncommitted_kg <= 0:
        return COMMITTED, "살아 있는 할당이 잔량을 다 덮었다"
    return REDETECT, None
