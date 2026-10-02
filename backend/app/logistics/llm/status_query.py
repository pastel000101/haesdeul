"""질문형 STATUS_QUERY 의 LLM 계약 — 지시문 · Tool 스키마 · 허용 Tool 이름.

루프는 `service/status_question.py` 가 돈다.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Tool schema — LLM 에게 여는 Read-only Tool (item_name 수준 · estimate_action_impact 제외)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: tuple[dict[str, Any], ...] = (
    {
        "name": "get_item_lots",
        "description": "한 품목의 Lot 목록과 현재고·판매가용·신선도. 품목명은 한국어(예: 배추).",
        "parameters": {
            "type": "object",
            "properties": {"item_name": {"type": "string", "description": "품목명 예: 배추"}},
            "required": ["item_name"],
        },
    },
    {
        "name": "get_sales_commitments",
        "description": "한 품목의 살아 있는 예약과 확정 출고.",
        "parameters": {
            "type": "object",
            "properties": {"item_name": {"type": "string"}},
            "required": ["item_name"],
        },
    },
    {
        "name": "get_capacity_context",
        "description": "창고 전체 용량·여유(품목 무관 · 창고 전체 기준).",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_inbound_schedule",
        "description": "입고 예정 목록. item_name 을 주면 그 품목만, 비우면 프로젝트 품목 전체.",
        "parameters": {
            "type": "object",
            "properties": {"item_name": {"type": "string"}},
            "required": [],
        },
    },
    {
        "name": "get_open_exceptions",
        "description": "지금 열려 있는 물류 문제. item_name 을 주면 그 품목 관련만.",
        "parameters": {
            "type": "object",
            "properties": {"item_name": {"type": "string"}},
            "required": [],
        },
    },
    {
        "name": "get_policy",
        "description": "보관·회전·용량 정책. item_name 을 주면 그 품목 정책, 비우면 전역.",
        "parameters": {
            "type": "object",
            "properties": {"item_name": {"type": "string"}},
            "required": [],
        },
    },
    {
        "name": "get_lot",
        "description": "Lot 하나의 상태·잔량·신선도.",
        "parameters": {
            "type": "object",
            "properties": {"lot_id": {"type": "string"}},
            "required": ["lot_id"],
        },
    },
)

ALLOWED_TOOL_NAMES = frozenset(schema["name"] for schema in TOOL_SCHEMAS)

SYSTEM_PROMPT = (
    # ── 조회 규율 (Tool 호출 단계) ──────────────────────────────────────
    "너는 창고 물류 상태를 **조회만** 하는 도우미다. 사용자의 질문에 답하려면 제공된 "
    "Read-only Tool 을 직접 선택해 호출하라. 품목은 한국어 이름(배추·무·양파)으로 넘기고, "
    "내부 item_id 를 지어내지 마라. 재고·신선도·예약·용량 같은 숫자를 직접 계산하거나 "
    "합산·환산하지 말고 반드시 Tool 결과에 있는 값을 그대로 사용하라. 여러 품목을 물으면 "
    "품목마다 Tool 을 호출하라. 어떤 품목이 프로젝트 조회 대상이 아니면 Tool 이 그렇게 "
    "알려준다 — 그 품목은 빼고 나머지는 정상 조회하라.\n\n"
    # ── 최종 답변 규율 ────────────────────────────────────────────────
    "충분한 정보를 모으면 한국어로 최종 답변을 하라. 최종 답변은 **사용자에게 그대로 보이는 "
    "글**이므로 다음을 지켜라.\n"
    "1. 질문에 대한 핵심 답을 **첫 문장에 바로** 말하라. "
    '예: "2026-08-31 기준 배추 재고는 총 3,197kg입니다."\n'
    "2. 내부 구현 정보를 답변에 **절대 노출하지 마라** — Tool 이름, 호출 인자, tool_trace, "
    "tools_used, 내부 item_id(ITEM-BAECHU 등), raw 결과, 내부 스키마/필드 이름, 상태 코드, "
    "provider 이름, 디버그 metadata. 사용자는 이런 것을 묻지 않았다.\n"
    "3. 사용자가 Lot 상세를 **명시적으로 요청하지 않았다면 Lot 을 나열하지 마라.** 특히 "
    "잔량이 0인 Lot, 소진(DEPLETED)·폐기된 Lot, 과거 이력 Lot 은 일반 재고 질문의 답변에서 "
    "생략하라.\n"
    "4. Lot 은 필요할 때만 **현재 재고가 남아 있는 것의 개수로 요약**하라. "
    '예: "현재 재고가 남아 있는 Lot은 5개입니다."\n'
    "5. 신선도·위험 정보는 질문과 관련 있을 때만 **한 문장으로 짧게** 덧붙여라. "
    '예: "이 중 514kg은 신선도가 1일 남아 판매 우선 대상입니다."\n'
    "6. 불확실한 점이 없으면 굳이 «불확실성 없음» 같은 말을 하지 마라.\n"
    "7. 조회 대상이 아닌 품목은 **사용자가 이해할 말로만** 설명하라. "
    '예: "피마늘은 현재 프로젝트 조회 대상에 포함되지 않아 조회하지 않았습니다." '
    "내부 상태 문자열(NOT_IN_PROJECT_SCOPE 등)을 그대로 쓰지 마라.\n"
    "8. 창고 여유는 창고 전체 기준이다. 내부 값(warehouse_scope=global 등)을 그대로 보이지 "
    '말고 자연어로 설명하라. 예: "창고 전체 기준 현재 여유 용량은 7,375kg입니다."\n'
    "9. 답변은 짧게 유지하라 — 사용자가 상세 설명을 요청하지 않았으면 **2~5문장**을 넘기지 "
    "마라. 표·목록·머리말·«조회 결과입니다» 같은 군더더기를 붙이지 마라."
)
