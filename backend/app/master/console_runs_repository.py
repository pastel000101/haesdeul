"""콘솔 실행 목록 SQL — `sim_runs` 와 각 실행의 마지막 기록 시각.

★ 2026-09-29 재구성 BL-014: 화면 `api/console/runs.py` 가 재무 DB 입구(`app.finance.db`)의
  조회 헬퍼로 직접 돌리던 SQL 을 옮겼다(문면 · 인자 그대로). 조회는 마스터 입구
  `app.master.db.fetch_all` 이 조회 연결을 빌려 한다. 응답 조립은 `readmodel/console_runs.py`.
"""

from psycopg import sql

from app.master.db import fetch_all, get_db_schema


def read_console_runs(*, limit: int) -> list[dict[str, object]]:
    """실행 행과, 각 실행에 마지막으로 기록이 쌓인 시각.

    ★ 정렬은 **최근 활동 → 기준일 → 이름** 순이다. 활동이 없는 실행이 목록에서
      사라지지 않도록 `NULLS LAST` 로 뒤에 세운다 — 아직 안 걸은 실행도 고를 수
      있어야 한다.
    """
    schema = get_db_schema()
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
    return fetch_all(statement, [limit])
