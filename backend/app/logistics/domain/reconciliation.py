"""고착 입고 일정 정리의 입력 검사.

★ 2026-09-30 재구성 BL-015: `logistics/inbound_reconciliation.py` 에서 옮겼다.
"""

from __future__ import annotations

from typing import Any

from app.logistics.schemas.reconciliation import InvalidReconciliationRequest


def reconciliation_text(value: Any, *, 칸: str) -> str:
    """비었거나 공백뿐이면 **DB 에 묻기 전에** 멈춘다."""
    if not isinstance(value, str) or not value.strip():
        raise InvalidReconciliationRequest(
            f"입고 일정 정리에 쓸 수 없는 {칸} 다: {value!r}."
            " 빈 축으로 물으면 0건이 돌아오고 그 0건은 '이미 걷혔다' 로 읽힌다 —"
            " 없는 것과 물어보지 못한 것은 다른 사실이다."
        )
    return value
