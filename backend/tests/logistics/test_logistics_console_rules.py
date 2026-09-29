"""물류 콘솔 판정 두 개 — `app/logistics/domain/console_rules.py` (재구성 BL-012).

2026-09-29 전에는 화면 모듈(`app/api/logistics/query.py`)의 비공개 함수였다. 판정을 물류
domain 으로 올리면서 **입력 → 출력 표**를 여기서 DB 없이 잠근다. 실 PostgreSQL 에서
감지 이력을 쌓아 보는 검사는 `test_logistics_monitoring_exceptions_db.py`(`db` 마커)에 있다.

```text
still_working   그날 아직 일이 남은 예약인가      화면 3곳 + 재고·물류 보고서가 같은 함수
severity_at     그날 우선도 코드 · 증명 불가 None  사람 말은 화면(`_severity_label`)이 정한다
```
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.logistics.domain.console_rules import severity_at, still_working
from app.logistics.monitoring.schemas import DetectionRecord, ExceptionEvidence, ExceptionRow

D1 = date(2026, 1, 20)
D2 = D1 + timedelta(days=1)
D3 = D1 + timedelta(days=2)


# ── still_working ────────────────────────────────────────────────────────


def _resv(status, allocated=Decimal(0), unallocated=Decimal(0), shipped=()):
    return SimpleNamespace(
        status=status,
        allocated_qty_kg=allocated,
        unallocated_qty_kg=unallocated,
        allocations=[SimpleNamespace(status=s) for s in shipped],
    )


@pytest.mark.parametrize(
    ("reservation", "expected"),
    [
        #  Lot 을 다 못 골랐다 — 잡은 양이 0 이어도 남았다
        (_resv("RESERVED"), True),
        (_resv("PARTIALLY_ALLOCATED", shipped=("SHIPPED",)), True),
        #  놓아줬다 — 잡은 양이 있어도 끝났다
        (_resv("RELEASED", allocated=Decimal(5)), False),
        (_resv("CANCELLED", unallocated=Decimal(5)), False),
        #  ALLOCATED: 잡은 양이 있으면 남았다
        (_resv("ALLOCATED", allocated=Decimal("0.5")), True),
        (_resv("ALLOCATED", unallocated=Decimal(1), shipped=("SHIPPED",)), True),
        #  ALLOCATED · 잡은 양 0: SHIPPED 할당이 있어야 끝났다
        (_resv("ALLOCATED", shipped=("SHIPPED",)), False),
        (_resv("ALLOCATED", shipped=("ALLOCATED", "SHIPPED")), False),
        (_resv("ALLOCATED", shipped=("ALLOCATED",)), True),
        (_resv("ALLOCATED"), True),
    ],
    ids=[
        "RESERVED", "PARTIAL", "RELEASED", "CANCELLED", "ALLOCATED_잡은양",
        "ALLOCATED_미할당", "전량출고", "일부출고_잡은양0", "출고없음", "할당없음",
    ],
)
def test_still_working_marks_reservations_with_work_left(reservation, expected):
    assert still_working(reservation) is expected


# ── severity_at ──────────────────────────────────────────────────────────


def _row(*history: tuple[date, str], last: date | None = D2, severity: str = "HIGH"):
    return ExceptionRow(
        exception_id="EX-1",
        sim_run_id="SIM-TEST",
        code="FRESHNESS_PRESSURE",
        subject_type="LOT",
        subject_id="LOT-1",
        severity=severity,
        status="OPEN",
        opened_as_of=D1,
        last_detected_as_of=last,
        observed_as_of=None,
        evidence=(
            ExceptionEvidence(fact="f", value=Decimal(1), unit="kg", source="s", source_id="x"),
        ),
        detector_version="v1",
        detection_history=tuple(DetectionRecord(as_of=d, severity=s) for d, s in history),
    )


def test_severity_at_picks_latest_detection_on_or_before_as_of():
    row = _row((D1, "LOW"), (D3, "CRITICAL"), (D2, "HIGH"))  # 저장 순서가 날짜순이 아니다

    assert [severity_at(row, d) for d in (D1, D2, D3, D3 + timedelta(days=5))] == [
        "LOW",
        "HIGH",
        "CRITICAL",
        "CRITICAL",
    ]


def test_severity_at_ignores_detections_after_as_of():
    """🔴 이력은 있는데 기준일 이하 감지가 없다 — 지금 값(`severity`)으로 메우지 않는다."""
    row = _row((D3, "CRITICAL"), severity="CRITICAL", last=D3)

    assert severity_at(row, D2) is None


def test_severity_at_keeps_the_first_entry_on_the_same_day():
    """날짜가 같으면 먼저 나온 원소를 고른다 — 옮기기 전 규칙 그대로(더 늦은 날짜만 바꾼다)."""
    row = _row((D2, "LOW"), (D2, "HIGH"))

    assert severity_at(row, D2) == "LOW"


@pytest.mark.parametrize(
    ("last", "as_of", "expected"),
    [
        (D2, D3, "HIGH"),  # 마지막 감지가 기준일 이전 → 지금 값이 그날 값
        (D2, D2, "HIGH"),  # 같은 날
        (D2, D1, None),  # 마지막 감지가 기준일 뒤 → 증명 불가
        (None, D3, None),  # 마지막 감지일을 모른다
    ],
)
def test_severity_at_uses_last_detected_date_without_history(last, as_of, expected):
    assert severity_at(_row(last=last), as_of) == expected


def test_severity_at_returns_unknown_codes_unchanged():
    """★ 코드를 사람 말로 바꾸는 것은 화면 일이다 — 판정은 코드를 고치지 않는다."""
    assert severity_at(_row((D1, "WEIRD")), D1) == "WEIRD"
