"""질문형 STATUS_QUERY 의 결정론 규칙 — 프로젝트 품목 gate · 품목 해석 · 답 숫자 표기."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from app.logistics.schemas.status_question import ResolvedItems

# ---------------------------------------------------------------------------
# 프로젝트 품목 범위 — 업무 결정이다 (DB 정책/Zone 유무가 아니다)
# ---------------------------------------------------------------------------

#: 프로젝트 정상 조회 대상 (이름 → item_id). item_id 는 언제나 `resolve_item_names`(DB)가
#: 확정한다 — 이 상수는 allowlist 판정과 이름 표기에만 쓴다.
PROJECT_ITEMS: dict[str, str] = {
    "배추": "ITEM-BAECHU",
    "무": "ITEM-MU",
    "양파": "ITEM-YANGPA",
}
PROJECT_ITEM_IDS: frozenset[str] = frozenset(PROJECT_ITEMS.values())

#: DB 에 남아 있어도 현재 프로젝트 범위에서 제외된 품목.
EXCLUDED_ITEMS: dict[str, str] = {
    "피마늘": "ITEM-PIMANUL",
    "건고추": "ITEM-GEONGOCHU",
}

#: tool-calling loop 상한 — LLM 왕복 수와 총 tool 호출 수.
MAX_TURNS = 6
MAX_TOOL_CALLS = 12


def out_of_scope(name: str | None, status: str) -> dict[str, Any]:
    """제외/미확인 품목을 «실패가 아닌 사실» 로 LLM 에 돌려준다."""
    if status == "EXCLUDED":
        return {
            "item_name": name,
            "status": "NOT_IN_PROJECT_SCOPE",
            "message": "현재 프로젝트의 재고·물류 조회 대상 품목이 아닙니다.",
        }
    if status == "MISSING":
        return {"status": "ITEM_NAME_REQUIRED", "message": "품목명이 필요합니다."}
    return {
        "item_name": name,
        "status": "ITEM_NOT_FOUND",
        "message": "해당 품목을 찾을 수 없습니다.",
    }


# ---------------------------------------------------------------------------
# JSON 직렬화 (Tool 결과를 LLM 에 돌려줄 모양)
# ---------------------------------------------------------------------------


def answer_number(value: Decimal | float | None) -> float | None:
    return None if value is None else float(value)


def jsonify(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonify(v) for v in value]
    return value


def lot_dict(lot: Any) -> dict[str, Any]:
    return {
        "lot_id": lot.lot_id,
        "remaining_qty_kg": answer_number(lot.remaining_qty_kg),
        "uncommitted_kg": answer_number(lot.uncommitted_kg),
        "remaining_freshness_days": lot.remaining_freshness_days,
        "turnover_status": lot.turnover_status,
        "sell_priority": lot.sell_priority,
        "disposal_candidate": lot.disposal_candidate,
        "status": lot.status,
    }


def resolved_items(unique: Sequence[str], by_name: Mapping[str, str]) -> ResolvedItems:
    """DB 로 확정한 품목 → 프로젝트 허용 · 제외 · 없음으로 가른다(`PROJECT_ITEM_IDS` gate).

    DB 를 만지지 않는다.
    """
    allowed: dict[str, str] = {}
    excluded: dict[str, str] = {}
    not_found: list[str] = []
    for name in unique:
        item_id = by_name.get(name)
        if item_id is None:
            not_found.append(name)
        elif item_id in PROJECT_ITEM_IDS:
            allowed[name] = item_id
        else:
            excluded[name] = item_id
    return ResolvedItems(allowed=allowed, excluded=excluded, not_found=tuple(not_found))
