"""질문형 STATUS_QUERY — LLM function/tool calling 조회 (LOG-MDS-004 · Issue #789).

기존 Overview(`payload={}`)는 `service/agent_status.status_query_reply` 가 그대로 처리한다.
이 모듈은 `payload={"question": ...}` 일 때만 도는 경로다.

```text
question
  → LLM 에게 Read-only Tool schema 제공(item_name 수준)
  → LLM 이 부를 Tool 을 직접 선택하고 tool_call 생성
  → runtime 이 thin wrapper 로 실행 (item_name→item_id resolve + allowlist = 결정론)
  → Tool 결과를 다시 LLM 에게
  → LLM 이 추가 tool_call 또는 최종 자연어 답변
```

Tool 선택은 LLM 이 한다. topic→Tool 파이썬 고정 매핑을 쓰지 않는다(#789).

숫자·item_id 는 결정론이다. wrapper 가 `item_name` 을 DB 로 `item_id` 로 바꾸고
(마스터 `_item_id_of` 규약), 프로젝트 3품목(배추·무·양파)만 허용하고, 기존
Read-only Tool 을 시그니처 그대로 부른다. LLM 은 `item_id` 를 만들지 않는다.

LLM 이 primary 다. 결정론 파서 fallback 은 없다 — provider 실패는
`StatusQueryLLMError` 로 드러난다.

이 파일에는 LLM tool-calling 루프와 Tool 감싸기가 있다. 지시문 · Tool 스키마는
`llm/status_query.py`, 전송은 `llm/status_chat.py`, 품목 gate 는
`domain/status_question.py`, 품목 SQL 은 `repository/status_question.py` 다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from datetime import date
from decimal import Decimal
from typing import Any

from app.core import db as core_db
from app.logistics.domain.status_question import (
    MAX_TOOL_CALLS,
    MAX_TURNS,
    PROJECT_ITEM_IDS,
    answer_number,
    jsonify,
    lot_dict,
    out_of_scope,
    resolved_items,
)
from app.logistics.llm.status_chat import (
    AssistantTurn,
    Chat,
    StatusQueryLLMError,
    ToolCall,
    build_chat,
)
from app.logistics.llm.status_query import ALLOWED_TOOL_NAMES, SYSTEM_PROMPT, TOOL_SCHEMAS
from app.logistics.readmodel.status_tools import (
    get_capacity_context,
    get_inbound_schedule,
    get_item_lots,
    get_lot,
    get_open_exceptions,
    get_policy,
    get_sales_commitments,
)
from app.logistics.repository.status_question import select_item_ids_by_name
from app.logistics.schemas.status_question import ResolvedItems, StatusQueryAnswer


def resolve_item_names(conn: Any, names: Sequence[str]) -> ResolvedItems:
    """품목명 → `item_id` 를 DB `items` 로 확정하고 프로젝트 허용 여부로 가른다.

    마스터 `_item_id_of` 와 같은 `item_name → item_id` 규약. 중복 이름은 한 번만 조회.
    """
    unique = tuple(dict.fromkeys(name for name in names if name))
    if not unique:
        return ResolvedItems(allowed={}, excluded={}, not_found=())

    by_name = select_item_ids_by_name(conn, unique)

    return resolved_items(unique, by_name)


def resolve_one(conn: Any, name: str | None) -> tuple[str | None, str]:
    """한 품목명 → (item_id, 상태). 상태 ∈ ALLOWED · EXCLUDED · NOT_FOUND · MISSING."""
    if not name:
        return None, "MISSING"
    resolved = resolve_item_names(conn, [name])
    if name in resolved.allowed:
        return resolved.allowed[name], "ALLOWED"
    if name in resolved.excluded:
        return resolved.excluded[name], "EXCLUDED"
    return None, "NOT_FOUND"


# ---------------------------------------------------------------------------
# Tool wrapper — item_name → item_id resolve + allowlist(결정론) → 기존 Tool 호출
# ---------------------------------------------------------------------------


def _wrap_item_lots(
    conn: Any, *, sim_run_id: str, as_of: date, args: Mapping[str, Any]
) -> dict[str, Any]:
    name = args.get("item_name")
    item_id, status = resolve_one(conn, name)
    if status != "ALLOWED":
        return out_of_scope(name, status)
    answer = get_item_lots(conn, sim_run_id=sim_run_id, as_of=as_of, item_id=item_id)
    lots = answer.lots
    sellable = sum(
        (lot.uncommitted_kg for lot in lots if lot.uncommitted_kg is not None), start=Decimal(0)
    )
    return {
        "item_name": name,
        "item_id": item_id,
        "on_hand_kg": answer_number(sum((lot.remaining_qty_kg for lot in lots), start=Decimal(0))),
        "sellable_uncommitted_kg": answer_number(sellable),
        "lot_count": len(lots),
        "lots": [lot_dict(lot) for lot in lots],
        "uncertainties": list(answer.uncertainties),
    }


def _wrap_sales_commitments(
    conn: Any, *, sim_run_id: str, as_of: date, args: Mapping[str, Any]
) -> dict[str, Any]:
    name = args.get("item_name")
    item_id, status = resolve_one(conn, name)
    if status != "ALLOWED":
        return out_of_scope(name, status)
    answer = get_sales_commitments(conn, sim_run_id=sim_run_id, as_of=as_of, item_id=item_id)
    return {
        "item_name": name,
        "item_id": item_id,
        "reserved_qty_kg": answer_number(
            sum((r.reserved_qty_kg for r in answer.live_reservations), start=Decimal(0))
        ),
        "unallocated_kg": answer_number(answer.unallocated_kg),
        "live_reservation_count": len(answer.live_reservations),
        "next_due_date": jsonify(answer.next_due_date),
        "confirmed_outbound_by_date": jsonify(dict(answer.confirmed_outbound_by_date)),
        "uncertainties": list(answer.uncertainties),
    }


def _wrap_capacity(
    conn: Any, *, sim_run_id: str, as_of: date, args: Mapping[str, Any]
) -> dict[str, Any]:
    answer = get_capacity_context(conn, sim_run_id=sim_run_id, as_of=as_of)
    return {
        "warehouse_scope": "global",
        "note": "창고 전체 기준",
        "used_kg": answer_number(answer.used_kg),
        "guaranteed_kg": answer_number(answer.guaranteed_kg),
        "available_kg": answer_number(answer.available_kg),
        "window_usage_ratio": answer_number(answer.window_usage_ratio),
        "capacity_basis": answer.capacity_basis,
        "uncertainties": list(answer.uncertainties),
    }


def _wrap_inbound(
    conn: Any, *, sim_run_id: str, as_of: date, args: Mapping[str, Any]
) -> dict[str, Any]:
    name = args.get("item_name")
    allow_ids: frozenset[str]
    if name:
        item_id, status = resolve_one(conn, name)
        if status != "ALLOWED":
            return out_of_scope(name, status)
        allow_ids = frozenset({item_id}) if item_id else frozenset()
    else:
        allow_ids = PROJECT_ITEM_IDS
    answer = get_inbound_schedule(conn, sim_run_id=sim_run_id, as_of=as_of)
    schedules = [
        {
            "inbound_id": fact.inbound_id,
            "item_id": fact.item_id,
            "quantity_kg": answer_number(fact.quantity_kg),
            "expected_arrival_date": jsonify(fact.expected_arrival_date),
            "has_receipt": fact.has_receipt,
            "stock_applied": fact.stock_applied,
        }
        for fact in answer.schedules
        if fact.item_id in allow_ids
    ]
    return {
        "item_name": name,
        "schedules": schedules,
        "uncertainties": list(answer.uncertainties),
    }


def _wrap_exceptions(
    conn: Any, *, sim_run_id: str, as_of: date, args: Mapping[str, Any]
) -> dict[str, Any]:
    name = args.get("item_name")
    allowed_lot_ids: frozenset[str] | None = None
    if name:
        item_id, status = resolve_one(conn, name)
        if status != "ALLOWED":
            return out_of_scope(name, status)
        item_lots = get_item_lots(conn, sim_run_id=sim_run_id, as_of=as_of, item_id=item_id)
        allowed_lot_ids = frozenset(lot.lot_id for lot in item_lots.lots)
    answer = get_open_exceptions(conn, sim_run_id=sim_run_id, as_of=as_of)
    exceptions = []
    for fact in answer.exceptions:
        if allowed_lot_ids is not None and not (
            fact.subject_type == "LOT" and fact.subject_id in allowed_lot_ids
        ):
            continue
        exceptions.append(
            {
                "exception_id": fact.exception_id,
                "code": fact.code,
                "subject_type": fact.subject_type,
                "subject_id": fact.subject_id,
                "severity": fact.severity,
                "status": fact.status,
                "opened_as_of": jsonify(fact.opened_as_of),
                "open_days": fact.open_days,
            }
        )
    return {
        "item_name": name,
        "exceptions": exceptions,
        "uncertainties": list(answer.uncertainties),
    }


def _wrap_policy(
    conn: Any, *, sim_run_id: str, as_of: date, args: Mapping[str, Any]
) -> dict[str, Any]:
    name = args.get("item_name")
    item_id: str | None = None
    if name:
        item_id, status = resolve_one(conn, name)
        if status != "ALLOWED":
            return out_of_scope(name, status)
    view = get_policy(conn, sim_run_id=sim_run_id, as_of=as_of, item_id=item_id)
    return {
        "item_name": name,
        "policy_version": view.policy_version,
        "agent_policy": jsonify(dict(view.agent_policy)),
        "item_policy": None if view.item_policy is None else jsonify(vars(view.item_policy)),
        "uncertainties": list(view.uncertainties),
    }


def _wrap_lot(
    conn: Any, *, sim_run_id: str, as_of: date, args: Mapping[str, Any]
) -> dict[str, Any]:
    lot_id = args.get("lot_id")
    if not lot_id:
        return {"status": "LOT_ID_REQUIRED", "message": "lot_id 가 필요합니다."}
    view = get_lot(conn, sim_run_id=sim_run_id, as_of=as_of, lot_id=lot_id)
    if view.lot is None:
        return {
            "lot_id": lot_id,
            "status": "LOT_NOT_FOUND",
            "uncertainties": list(view.uncertainties),
        }
    if view.lot.item_id not in PROJECT_ITEM_IDS:
        return {
            "lot_id": lot_id,
            "item_id": view.lot.item_id,
            "status": "NOT_IN_PROJECT_SCOPE",
            "message": "현재 프로젝트의 조회 대상 품목이 아닙니다.",
        }
    return {"lot_id": lot_id, **lot_dict(view.lot), "item_id": view.lot.item_id}


_TOOL_EXECUTORS = {
    "get_item_lots": _wrap_item_lots,
    "get_sales_commitments": _wrap_sales_commitments,
    "get_capacity_context": _wrap_capacity,
    "get_inbound_schedule": _wrap_inbound,
    "get_open_exceptions": _wrap_exceptions,
    "get_policy": _wrap_policy,
    "get_lot": _wrap_lot,
}


def run_tool(
    name: str, arguments: Mapping[str, Any], *, conn: Any, sim_run_id: str, as_of: date
) -> dict[str, Any]:
    """LLM 이 부른 tool 하나를 실행한다. 결과·오류를 LLM 이 읽을 dict 로 돌려준다.

    알 수 없는 tool·실행 오류는 예외로 던지지 않고 사실로 돌려준다 — LLM 이 보고
    다른 Tool 을 부르거나 그 사실을 설명할 수 있게 한다.
    """
    executor = _TOOL_EXECUTORS.get(name)
    if executor is None:
        return {"error": "UNKNOWN_TOOL", "tool": name}
    try:
        return executor(conn, sim_run_id=sim_run_id, as_of=as_of, args=arguments or {})
    except Exception as error:  # noqa: BLE001 — 한 Tool 실패가 loop 를 무너뜨리지 않는다
        return {"error": "TOOL_FAILED", "tool": name, "detail": repr(error)}


def answer_status_question(
    *,
    question: str,
    sim_run_id: str,
    as_of: date,
    chat: Chat | None = None,
    conn: Any = None,
) -> StatusQueryAnswer:
    """질문형 STATUS_QUERY 를 LLM tool-calling loop 로 처리한다.

    :param chat: LLM 전송 seam(기본 = 설정된 provider). 테스트는 가짜 chat 을 준다.
    :param conn: 이미 열린 커넥션(테스트/재사용). 없으면 Tool 을 부를 때마다 공통 풀에서
                 조회 연결을 빌리고 그 Tool 이 끝나면 돌려준다.

    LLM 을 기다리는 동안 연결을 쥐지 않는다. 루프 앞에서 연결 하나를 열어 LLM 턴 내내
    쥐고 있으면 풀 연결이 LLM 대기 시간만큼 묶인다. Tool 은 전부 읽기라 Tool 마다 빌려도
    답이 같다(READ COMMITTED 는 문장마다 새 스냅숏).
    """
    llm = chat if chat is not None else build_chat()
    borrow = core_db.read_connection if conn is None else (lambda: nullcontext(conn))
    return _run_loop(question, sim_run_id=sim_run_id, as_of=as_of, chat=llm, borrow=borrow)


def _run_loop(
    question: str, *, sim_run_id: str, as_of: date, chat: Chat, borrow: core_db.Borrow
) -> StatusQueryAnswer:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    tool_trace: list[dict[str, Any]] = []
    tool_calls_made = 0

    for _ in range(MAX_TURNS):
        turn: AssistantTurn = chat(messages, TOOL_SCHEMAS)
        if not turn.tool_calls:
            return _final_answer(
                question, as_of=as_of, final_text=turn.text or "", tool_trace=tool_trace
            )

        messages.append(
            {
                "role": "assistant",
                "tool_calls": [
                    # `raw`(공급자 원본 파트)를 함께 나른다 — Gemini 는 되돌려줄 때
                    # `thoughtSignature` 를 그대로 요구한다(새로 만들어 보내면 400).
                    {
                        "id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                        "raw": call.raw,
                    }
                    for call in turn.tool_calls
                ],
            }
        )
        for call in turn.tool_calls:
            if tool_calls_made >= MAX_TOOL_CALLS:
                result: dict[str, Any] = {"error": "TOOL_BUDGET_EXCEEDED", "tool": call.name}
            else:
                tool_calls_made += 1
                with borrow() as conn:
                    result = _run_one_tool(call, conn=conn, sim_run_id=sim_run_id, as_of=as_of)
            tool_trace.append(
                {"tool": call.name, "arguments": dict(call.arguments), "result": result}
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                }
            )

    raise StatusQueryLLMError(
        f"tool-calling 이 {MAX_TURNS}턴 안에 최종 답을 못 냈다", kind="NO_FINAL_ANSWER"
    )


def _run_one_tool(call: ToolCall, *, conn: Any, sim_run_id: str, as_of: date) -> dict[str, Any]:
    if call.name not in ALLOWED_TOOL_NAMES:
        return {"error": "UNKNOWN_TOOL", "tool": call.name}
    return run_tool(call.name, call.arguments, conn=conn, sim_run_id=sim_run_id, as_of=as_of)


def _final_answer(
    question: str, *, as_of: date, final_text: str, tool_trace: Sequence[Mapping[str, Any]]
) -> StatusQueryAnswer:
    tools_used = [entry["tool"] for entry in tool_trace]
    excluded = [
        entry["arguments"].get("item_name")
        for entry in tool_trace
        if isinstance(entry["result"], Mapping)
        and entry["result"].get("status") == "NOT_IN_PROJECT_SCOPE"
    ]
    failed = [
        entry["tool"]
        for entry in tool_trace
        if isinstance(entry["result"], Mapping) and entry["result"].get("error")
    ]
    # 답 전체를 한 Mapping 키 아래 둔다 — 최상위에 숫자를 두지 않아 근거(evidence)
    # 요구를 만들지 않는다. 숫자의 정본은 tool_trace 의 Tool 결과다.
    payload = {
        "status_query": {
            "question": question,
            "as_of": as_of.isoformat(),
            "answer": final_text,
            "tools_used": tools_used,
            "tool_trace": list(tool_trace),
            "excluded_items": [name for name in excluded if name],
        }
    }
    missing: list[str] = []
    if excluded:
        missing.append("excluded_items")
    if failed:
        missing.extend(f"tool_failed:{name}" for name in dict.fromkeys(failed))
    reasoning = f"질문형 물류 상태를 조회했다 (Tool {len(tools_used)}회)."
    return StatusQueryAnswer(payload=payload, missing_data=tuple(missing), reasoning=reasoning)
