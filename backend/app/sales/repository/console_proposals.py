"""그날 만든 판매안 SQL — 판매 이력의 안, 재무가 남긴 판정, 확정된 판매.

★ 2026-09-29 BL-013: `sales/console_proposals.py` 에서 SQL 을 옮겼다. 저장된 안을 응답으로
  펴는 조립은 `readmodel/console_proposals.py`, 안의 자리 판정은 `domain/console_proposals.py`.
"""

from datetime import date
from typing import Any

from psycopg import Connection, sql

from app.core.settings import get_db_schema
from app.sales.repository._cursor import fetch_all


def load_proposal_rows(conn: Connection, *, sim_run_id: str, as_of: date) -> list[dict[str, Any]]:
    """그날 판매가 만든 안과 재무가 남긴 판정.

    ★ **요청 키 하나에 행 하나를 고른다.** 되먹임(refeed)이 돌면 같은 요청이 여러 번
      저장되고, 그중 마지막이 그날의 답이다.

    ★ 재무 판정도 **가장 최근 것**을 읽는다. 재검증이 돌면 같은 키에 여러 회신이 쌓인다.

    🔴 **재무 판정에도 실행 축을 건다.** `request_id` 만으로 잇는 것은 안전해 보이지만
       아니다 — 축을 담지 않는 키가 실제로 있고(`REQ-DAILY-SALES-20260107-배추` 는 네
       실행에 걸쳐 있다), 그때는 남의 실행 판정이 이 안에 붙는다.

    ★ **축은 마스터가 안다.** `finance_agent_runs_v22` 에는 `sim_run_id` 칸이 없어,
      그 연결을 기록한 `master_agent_runs` 에 묻는다 — 재무 자신의 실행 이력 read model
      (`finance.console_runs`)이 같은 자리에서 같은 방법을 쓴다.

    ⚠️ **키 문자열을 파싱하지 않는다.** `REQ-DAILY-SALES-{실행}-…` 모양에 기대면 그
      모양이 바뀌는 날 화면이 오류 없이 남의 실행을 가리킨다. `request_id` 는 업무
      키이지 스키마가 아니다.
    """
    schema = get_db_schema()
    statement = sql.SQL(
        """
        SELECT latest.request_id,
               latest.payload,
               history.run_id AS history_run_id,
               scenario.value AS scenario,
               finance.finance_verdict,
               finance.finance_status,
               finance.rule_results,
               finance.financial_summary
        FROM (
            SELECT DISTINCT ON (run.response_payload->>'request_id')
                   run.response_payload->>'request_id' AS request_id,
                   run.response_payload->'payload' AS payload
            FROM {schema}.sales_agent_runs run
            WHERE run.request_payload->'context'->>'sim_run_id' = %s
              AND run.as_of = %s
              AND run.response_payload->>'request_id' IS NOT NULL
            ORDER BY run.response_payload->>'request_id', run.created_at DESC
        ) AS latest
        LEFT JOIN LATERAL (
            SELECT axis.run_id
            FROM {schema}.master_agent_runs axis
            WHERE axis.cycle = 'SALES'
              AND axis.request_id = latest.request_id
              AND axis.sim_run_id = %s
            ORDER BY axis.run_seq DESC, axis.created_at DESC
            LIMIT 1
        ) AS history ON TRUE
        LEFT JOIN LATERAL jsonb_array_elements(
            COALESCE(latest.payload->'scenarios', '[]'::jsonb)
        ) AS scenario ON TRUE
        LEFT JOIN LATERAL (
            SELECT check_run.response_payload->>'finance_verdict' AS finance_verdict,
                   check_run.response_payload->>'status' AS finance_status,
                   check_run.response_payload->'rule_results' AS rule_results,
                   check_run.response_payload->'financial_summary' AS financial_summary
            FROM {schema}.finance_agent_runs_v22 check_run
            WHERE check_run.mode = 'SALES_VALIDATION'
              AND check_run.request_id = latest.request_id
              AND check_run.response_payload->>'scenario_id' = scenario.value->>'scenario_id'
              AND EXISTS (
                  SELECT 1
                  FROM {schema}.master_agent_runs axis
                  WHERE axis.request_id = check_run.request_id
                    AND axis.sim_run_id = %s
              )
            ORDER BY check_run.created_at DESC
            LIMIT 1
        ) AS finance ON TRUE
        ORDER BY latest.request_id ASC, scenario.value->>'scenario_id' ASC
        """
    ).format(schema=sql.Identifier(schema))
    #  ⚠️ `%s` 는 네 개다 — 판매 실행 축, 기준일, 화면이 본 마스터 실행, 재무 판정 축.
    return fetch_all(conn, statement, [sim_run_id, as_of, sim_run_id, sim_run_id])


def load_sale_statuses(
    conn: Connection, *, sim_run_id: str, request_ids: list[str]
) -> dict[tuple[str, str], str]:
    """그날 요청에서 **실제로 확정된 판매**. `(요청 키, 안 번호) → 주문 상태`.

    ★ 판매 확정은 `sales` 행으로 남는다 (`domain/sale_ledger.build_sale_confirmation_plan`).
      `source_order_id` 가 요청 키이고 `sale_id` 끝이 안 번호다 (`sale_id_for`).

    🔴 **실행 축을 건다.** 같은 요청 키가 다른 실행에도 있을 수 있다.
    """
    if not request_ids:
        return {}
    schema = get_db_schema()
    statement = sql.SQL(
        """
        SELECT sale.source_order_id, sale.sale_id, sale.order_status
        FROM {schema}.sales sale
        WHERE sale.sim_run_id = %s
          AND sale.source_order_id = ANY(%s)
        """
    ).format(schema=sql.Identifier(schema))
    found: dict[tuple[str, str], str] = {}
    for raw in fetch_all(conn, statement, [sim_run_id, request_ids]):
        request_id = str(raw["source_order_id"])
        sale_id = str(raw["sale_id"])
        found[(request_id, sale_id)] = str(raw["order_status"])
    return found
