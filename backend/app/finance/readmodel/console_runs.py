"""Finance operations-console run-history read model; strictly run-scoped.

The run axis is not stored on the Finance run row.  `finance_agent_runs_v22`
keeps `request_id` but no `sim_run_id`, so this reader asks the one table that does
record that binding — Master's `master_agent_runs`, which wrote the request in the
first place.  It is read, never written, and nothing is copied out of it beyond the
axis it owns.

The alternative would be parsing a run id out of the request-id string.  That reads
like it works and is a guess: a request id is a business key, not a schema, and the
day its shape changes the console starts attributing runs to the wrong simulation
without any error to notice.

A Finance run whose request never reached Master therefore belongs to no run here
and is not returned.  That is the honest answer — there is no stored fact that puts
it in one.

응답 모델은 `schemas/console_runs.py`, SQL 은 `repository/console_runs.py`.
"""

from datetime import date

from app.core import db as core_db
from app.finance.repository.console_runs import select_console_finance_runs
from app.finance.schemas.console_runs import ConsoleFinanceRun, ConsoleFinanceRunsResponse


def console_finance_run(raw: dict[str, object], *, sim_run_id: str) -> ConsoleFinanceRun:
    response = raw["response_payload"] if isinstance(raw["response_payload"], dict) else {}
    verdict = response.get("finance_verdict")
    summary = response.get("financial_summary")
    evidence = response.get("evidence_refs")
    return ConsoleFinanceRun(
        run_id=str(raw["run_id"]),
        request_id=str(raw["request_id"]),
        sim_run_id=sim_run_id,
        as_of=raw["as_of"],
        mode=str(raw["mode"]),
        runtime_status=str(raw["runtime_status"]),
        business_status=str(raw["business_status"]),
        verdict=None if verdict is None else str(verdict),
        llm_status=str(raw["llm_status"]),
        deterministic_result=summary if isinstance(summary, dict) else None,
        evidence=[str(item) for item in evidence] if isinstance(evidence, list) else None,
        interpretation=None,
        created_at=raw["created_at"],
    )


def get_console_finance_runs(
    *,
    sim_run_id: str,
    as_of: date | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    runtime_status: str | None = None,
    verdict: str | None = None,
    limit: int = 100,
) -> ConsoleFinanceRunsResponse:
    """Stored Finance runs belonging to exactly one simulation run."""
    with core_db.read_connection() as conn:
        rows = select_console_finance_runs(
            conn,
            sim_run_id=sim_run_id,
            as_of=as_of,
            from_date=from_date,
            to_date=to_date,
            runtime_status=runtime_status,
            verdict=verdict,
            limit=limit,
        )
    return ConsoleFinanceRunsResponse(
        sim_run_id=sim_run_id,
        rows=[console_finance_run(raw, sim_run_id=sim_run_id) for raw in rows],
    )


def get_console_finance_latest_run(
    *, sim_run_id: str, as_of: date | None = None
) -> ConsoleFinanceRun | None:
    """The newest stored Finance run inside this simulation run, or null.

    This reads history.  It never calls the agent — a GET that re-runs Finance
    would make opening a screen change the ledger.

    There is no global fallback.  If this run has no Finance run yet, the answer
    is "none", not somebody else's newest run.
    """
    with core_db.read_connection() as conn:
        rows = select_console_finance_runs(
            conn,
            sim_run_id=sim_run_id,
            as_of=as_of,
            from_date=None,
            to_date=None,
            runtime_status=None,
            verdict=None,
            limit=1,
        )
    return None if not rows else console_finance_run(rows[0], sim_run_id=sim_run_id)
