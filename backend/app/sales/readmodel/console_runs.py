"""Sales operations-console run-history read model; strictly run-scoped.

★ **The run axis is stored on the row itself.**  Sales writes its execution context
  into `sales_agent_runs.request_payload->'context'`, and `sim_run_id` is one of its
  keys, so this reader filters on the stored value rather than on anything derived
  from a request-id string.

🔴 **Reading history never re-runs the agent.**  A GET that executed Sales would make
  opening a screen write to the ledger.

★ 2026-09-29 BL-013: `sales/console_runs.py` 에서 옮겼다. SQL 은 `repository/console_runs.py`.
"""

from datetime import date
from typing import Any

from app.core import db as core_db
from app.sales.repository.console_runs import load_run_rows
from app.sales.schemas.console_runs import ConsoleSalesRun, ConsoleSalesRunsResponse


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _payload_item(payload: object, *keys: str) -> Any:
    current = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _row(raw: dict[str, object], *, sim_run_id: str) -> ConsoleSalesRun:
    request = raw["request_payload"]
    response = raw["response_payload"]
    return ConsoleSalesRun(
        run_id=str(raw["run_id"]),
        request_id=_text(_payload_item(request, "context", "request_id")),
        sim_run_id=sim_run_id,
        as_of=raw["as_of"],
        partner_id=_text(_payload_item(request, "payload", "user_request", "partner_id")),
        partner_name=_text(raw["partner_name"]),
        item=_text(_payload_item(request, "payload", "user_request", "item")),
        runtime_status=str(raw["runtime_status"]),
        verdict=None,
        llm_status=_text(_payload_item(response, "payload", "llm", "llm_status")),
        master_end_code=_text(raw["master_end_code"]),
        created_at=raw["created_at"],
    )


def get_console_sales_runs(
    *,
    sim_run_id: str,
    as_of: date | None = None,
    partner_id: str | None = None,
    item: str | None = None,
    runtime_status: str | None = None,
    limit: int = 100,
) -> ConsoleSalesRunsResponse:
    """Stored Sales runs belonging to exactly one simulation run."""
    with core_db.read_connection() as conn:
        rows = load_run_rows(
            conn,
            sim_run_id=sim_run_id,
            as_of=as_of,
            partner_id=partner_id,
            item=item,
            runtime_status=runtime_status,
            limit=limit,
        )
    return ConsoleSalesRunsResponse(
        sim_run_id=sim_run_id, rows=[_row(raw, sim_run_id=sim_run_id) for raw in rows]
    )
