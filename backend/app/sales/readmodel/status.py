"""마스터 STATUS_QUERY 가 읽는 판매 진행 사실 — 이 실행의 최근 판단 이력과 그날의 판매안.

★ 2026-09-29 BL-013: `sales/adapter.py` 의 `_status_query` · `_today_proposals` 에서 조회와
  실행 축 거르기를 옮겼다. 사람이 읽는 사실은 `domain/status_facts.py`, 봉투 회신으로 옮기는
  번역은 `adapter.py` 다. 읽는 순서(이력 → 그날 판매안)와 못 읽었을 때의 처리는 그대로다.
"""

import logging
from dataclasses import dataclass
from datetime import date

from app.sales.readmodel.console_proposals import get_console_sales_proposals
from app.sales.readmodel.runs import list_sales_runs
from app.sales.schemas.console_proposals import ConsoleSalesProposalsResponse
from app.sales.schemas.runs import SalesAgentRunResponse

logger = logging.getLogger(__name__)


#: 실행 축으로 거르기 전에 훑는 범위. 같은 날 여러 실행이 섞여 있어도 우리 것이 남는다.
_STATUS_SCAN_LIMIT = 200

#: 답에 싣는 최대 실행 수. 사람이 읽는 요약이라 길게 낼 이유가 없다.
_STATUS_RUN_LIMIT = 5


@dataclass(frozen=True)
class SalesStatusView:
    """이 실행의 판매 진행 사실.

    ★ `runs` 가 `None` 이면 **이력을 읽지 못한 것**이다 — 이 실행의 이력이 0건인 것(`[]`)과
      다르다. 그때는 그날 판매안도 읽지 않는다(종전 순서 그대로).
    """

    runs: list[SalesAgentRunResponse] | None
    proposals: ConsoleSalesProposalsResponse | None


def read_sales_status(*, sim_run_id: str | None, as_of: date) -> SalesStatusView:
    """이 실행 축의 최근 판단 이력(최대 `_STATUS_RUN_LIMIT` 건)과 그날의 판매안."""
    try:
        #  🔴 **거르기 전에 넓게 읽는다.** `list_sales_runs` 에는 실행 축 필터가 없어
        #     5건만 받아 거르면, 그 5건이 전부 남의 실행일 때 «이력이 없습니다» 가 된다.
        #     실제로 2026-01-26 이 그랬다 — 같은 날 다른 실행의 기록이 더 최신이었다.
        runs = list_sales_runs(as_of=as_of, limit=_STATUS_SCAN_LIMIT)
    except Exception:  # noqa: BLE001
        return SalesStatusView(runs=None, proposals=None)

    #  🔴 **이 실행의 이력만 답한다.** `list_sales_runs` 에는 실행 축 필터가 없어
    #     그대로 내면 남의 실행 이력이 «우리 판매 진행 상황» 으로 나간다.
    scoped = [run for run in runs if _run_axis(run) == sim_run_id][:_STATUS_RUN_LIMIT]
    return SalesStatusView(
        runs=scoped, proposals=_today_proposals(sim_run_id=sim_run_id, as_of=as_of)
    )


def _today_proposals(
    *, sim_run_id: str | None, as_of: date
) -> ConsoleSalesProposalsResponse | None:
    """그날의 판매안과 재무 판정. **못 읽으면 `None` 이다** — 빈 목록으로 바꾸지 않는다."""
    if not sim_run_id:
        return None
    try:
        return get_console_sales_proposals(sim_run_id=sim_run_id, as_of=as_of)
    except Exception:  # noqa: BLE001 - 못 읽은 것을 «판매안 없음» 으로 말하지 않는다.
        logger.warning("sales status could not read today's proposals: %s", sim_run_id)
        return None


def _run_axis(run: SalesAgentRunResponse) -> str | None:
    """이 실행 기록이 어느 실행 축에 속하는지. **봉투 안에 적혀 있다.**"""
    context = run.request_payload.get("context")
    if not isinstance(context, dict):
        return None
    axis = context.get("sim_run_id")
    return None if axis is None else str(axis)
