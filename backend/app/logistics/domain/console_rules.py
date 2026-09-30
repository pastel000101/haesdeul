"""물류 콘솔이 읽은 값에 거는 판정 — 화면과 마스터 보고서가 함께 쓴다.

2026-09-29 (재구성 BL-012) 전에는 `app/api/logistics/query.py` 의 비공개 함수
`_still_working` · `_severity_at` 이었다. 마스터 보고서가 화면 모듈의 비공개 함수를 빌려
쓰느라 `master → api` 의존이 생겼고, 판정의 주인이 화면이었다. 판정만 여기로 올리고
사람이 읽는 말(우선도 이름 · «우선도 정보 없음»)은 화면에 남겼다.

★ **입력 값만 본다.** DB · HTTP · 시계 · 화면 문구를 쓰지 않는다. 타입은 주석에만 쓴다 —
  종전 `app.logistics.schemas` 는 실행 중에 불러오면 출고 · DB 모듈까지 따라 올라왔다.

★ 2026-09-30 재구성 BL-015: 타입의 자리가 `schemas/console.py` · `schemas/monitoring.py` 로
  옮겨 주석용 import 경로를 고쳤다. 이제 `schemas/` 는 다른 물류 계층을 들이지 않아 위
  문제는 없지만, 판정이 타입을 실행 중에 쓰지 않으므로 주석 전용은 그대로 둔다.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.logistics.schemas.console import ConsoleReservation
    from app.logistics.schemas.monitoring import DetectionRecord, ExceptionRow

__all__ = ["severity_at", "still_working"]


def still_working(r: ConsoleReservation) -> bool:
    """그날 **아직 일이 남은** 예약인가 — 발표 화면과 재고·물류 보고서가 그리는 모집단이다.

    🔴 **«완료» 를 할당 0 · 미할당 0 두 칸만으로 짐작하지 않는다** (#675 지시 §10).
       그날 유도한 상태(`status`)와 출고된 할당(`SHIPPED`)을 같이 본다.

    ```text
    RESERVED · PARTIALLY_ALLOCATED        아직 Lot 을 다 못 골랐다        → 남았다
    잡고 있는 양(allocated + unallocated) > 0                            → 남았다
    ALLOCATED 이고 잡은 양 0 · SHIPPED 할당 있음   전량 출고가 끝났다      → 끝났다
    RELEASED · CANCELLED                   놓아줬다                       → 끝났다
    ```

    ⚠️ **숨기는 것이 아니다.** 뺀 건수를 카드 footer 에 적는다 — Historical 은 그대로다.
    """
    if r.status in ("RESERVED", "PARTIALLY_ALLOCATED"):
        return True
    if r.status in ("RELEASED", "CANCELLED"):
        return False
    if r.allocated_qty_kg > 0 or r.unallocated_qty_kg > 0:
        return True
    return not any(a.status == "SHIPPED" for a in r.allocations)


def severity_at(row: ExceptionRow, as_of: date) -> str | None:
    """그날 우선도 코드(`LOW` · `MEDIUM` · `HIGH` · `CRITICAL`). 증명할 수 없으면 `None`.

    🔴 **미래 값을 과거 화면으로 흘리지 않는다.**

    `touch_exception` 이 `severity` 를 **덮어쓴다** (`SET severity = …`) — 그래서 지금
    행의 `severity` 는 마지막 감지값(캐시)이지 과거값이 아니다. LOG-AGENT-005 로
    `detection_history` 가 «그날 severity» 를 쌓으므로, 그 이력에서 그날 값을 복원한다.

    ```text
    detection_history 에 as_of <= 기준일 원소 있음  → 그중 max(as_of) 원소의 severity
                                                       🔴 배열 순서를 믿지 않는다 — 날짜로 고른다
    이력 있으나 기준일 이하 감지 없음               → None (그날 우선도 증명 불가)
    이력 없음(옛 행 · [])                          → 기존 fallback:
        last_detected_as_of <= as_of   지금 값이 그날 값이다
        그 밖                          None
    ```

    ★ 코드를 사람 말로 바꾸는 것과 `None` 일 때 적는 말은 화면이 정한다
      (`app/api/logistics/presenter.py::_severity_label`).
    """
    chosen: DetectionRecord | None = None
    for record in row.detection_history:
        if record.as_of <= as_of and (chosen is None or record.as_of > chosen.as_of):
            chosen = record
    if chosen is not None:
        return chosen.severity
    if row.detection_history:
        # 이력은 있으나 기준일 이하 감지가 없다 — 그날 우선도를 증명할 수 없다.
        return None
    # 이력 없는 옛 행(적용 전 생성) — 기존 규칙 그대로.
    detected = row.last_detected_as_of
    if detected is None or detected > as_of:
        return None
    return row.severity
