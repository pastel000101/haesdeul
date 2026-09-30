"""마스터 이력 조회 — 실행 이력 · 매입안 보고서 · 번인 구간 응답을 조립한다.

★ 2026-09-30 재구성 BL-018: `master/service.py` 에서 옮겼다 — `get_run_report`,
  `get_burn_in_history`, `get_run_history`.
"""

from __future__ import annotations

from app.master.readmodel.decisions import list_decisions
from app.master.readmodel.ledger import get_burn_in
from app.master.readmodel.runs import get_run_by_request_id
from app.master.report.purchase_report import render_report, report_filename
from app.master.schemas.history import BurnInOut, DailyClosingOut, ReportOut, RunHistoryOut

# ---------------------------------------------------------------------------
# 조회 — GET /master/runs/{request_id}
# ---------------------------------------------------------------------------


def get_run_report(request_id: str) -> ReportOut:
    """저장된 실행 하나를 보고서로.

    ★ **최신 실행을 쓴다** — `get_run_history` 와 같은 규칙이다. *"그 요청 어떻게
      됐냐"* 에는 마지막 결과가 답이다.

    ★ 매입안 보고서다. 조회(STATUS)는 안이 없어 보고서가 성립하지 않으므로
      사이클을 밝힌다 (2026-09-02).
    """
    row = get_run_by_request_id(request_id, cycle="PROCUREMENT")
    run = dict(row.get("response_payload") or {})
    if not run:
        raise LookupError(f"실행 원문이 없어 보고서를 만들 수 없습니다: {request_id}")
    run.setdefault("request_id", request_id)
    item = row.get("item")
    # ★ 결정의 주인은 결정 표다. 문서는 **이 실행을 가리키는** 현재 결정만 상태로 적는다.
    run_id = str(row.get("run_id") or "")
    decision = next(
        (
            d.model_dump()
            for d in list_decisions(request_id)
            if d.is_current and (d.history_run_id is None or d.history_run_id == run_id)
        ),
        None,
    )
    return ReportOut(
        request_id=request_id,
        filename=report_filename(run, item=item),
        markdown=render_report(run, item=item, decision=decision),
    )


def get_burn_in_history() -> BurnInOut:
    """번인 구간(에이전트 판단 전 30일)을 화면이 읽는 형태로.

    ★ **값을 만들지 않는다.** DB 에 심긴 것을 모양만 바꾼다 — 합계·증감률을 여기서
      계산하기 시작하면 재무가 내는 숫자와 갈릴 자리가 생긴다. 화면이 필요하면
      **가진 값으로** 그린다.
    """
    raw = get_burn_in()
    run = raw["run"]
    return BurnInOut(
        sim_run_id=run["sim_run_id"],
        run_type=run["run_type"],
        period_start=run["period_start"],
        period_end=run["period_end"],
        as_of=run["as_of"],
        status=run["status"],
        financing_mode=run.get("financing_mode"),
        note=run.get("note"),
        closings=[DailyClosingOut(**row) for row in raw["closings"]],
    )


def get_run_history(request_id: str) -> RunHistoryOut:
    """업무 키로 실행 이력을 찾는다.

    ★ 재실행하면 같은 `request_id` 로 행이 여럿 생긴다. **최신을 돌려준다** —
      "그 요청 어떻게 됐냐"에는 마지막 결과가 답이다. 전체 이력이 필요하면
      `run_id` 로 목록을 훑는다.

    ★ 결정은 **전부** 싣는다 (실행과 달리 최신 하나로 접지 않는다).
      번복이 있었다는 사실 자체가 답의 일부다 — `is_current` 로 최신만 표시한다.
    """
    # ★ 조회(STATUS)가 같은 업무 키로 적재되므로 사이클을 밝힌다 (2026-09-02).
    #   이 화면은 매입 실행 이력이다 - 안 밝히면 최신 조회가 매입 자리에 뜬다.
    row = get_run_by_request_id(request_id, cycle="PROCUREMENT")
    plan = list(row.get("plan") or [])
    return RunHistoryOut(
        request_id=row.get("request_id") or request_id,
        run_id=(None if row.get("run_id") is None else str(row["run_id"])),
        as_of=row["as_of"],
        # ★ 표에 `agent` 컬럼이 없다 (2026-09-02, master_agent_runs 로 이전).
        #   마스터 전용 표라 늘 같은 값이었고, 상수를 컬럼으로 두면 "언젠가 다른 값이
        #   들어올 수 있다" 로 읽힌다. 응답 모양은 유지한다 - 화면이 쓰고 있다.
        agent="master",
        cycle=row["cycle"],
        runtime_status=row["runtime_status"],
        elapsed_ms=row.get("elapsed_ms"),
        created_at=row["created_at"],
        plan=plan,
        plan_signature=[
            (str(s.get("agent")), str(s.get("mode")), int(s.get("call_seq", 1))) for s in plan
        ],
        request_payload=dict(row.get("request_payload") or {}),
        response_payload=dict(row.get("response_payload") or {}),
        decisions=list_decisions(request_id),
    )
