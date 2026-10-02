"""콘솔 실행 목록 SQL — `sim_runs` 와 각 실행의 마지막 기록 시각.

받은 연결로 실행만 한다. 조회 연결 대여와 응답 조립은 `readmodel/console_runs.py` 다.
"""

from typing import Any

from psycopg import sql


def select_console_runs(conn: Any, *, limit: int, schema: str) -> list[dict[str, object]]:
    """실행 행과, 각 실행에 마지막으로 기록이 쌓인 시각.

    정렬은 최근 활동 → 기준일 → 이름 순이다. 활동이 없는 실행이 목록에서 사라지지
    않도록 `NULLS LAST` 로 뒤에 세운다 — 아직 안 걸은 실행도 고를 수 있어야 한다.
    """
    statement = sql.SQL(
        """
        SELECT r.sim_run_id, r.run_type, r.as_of, r.period_start, r.period_end,
               r.status, r.financing_mode, r.company_persona_id,
               r.started_at, r.finished_at, r.note,
               a.latest_activity_at
        FROM {schema}.sim_runs r
        LEFT JOIN LATERAL (
            SELECT max(created_at) AS latest_activity_at
            FROM {schema}.master_agent_runs
            WHERE sim_run_id = r.sim_run_id
        ) a ON TRUE
        ORDER BY a.latest_activity_at DESC NULLS LAST,
                 r.as_of DESC NULLS LAST,
                 r.sim_run_id ASC
        LIMIT %s
        """
    ).format(schema=sql.Identifier(schema))
    with conn.cursor() as cursor:
        cursor.execute(statement, [limit])
        return cursor.fetchall()
