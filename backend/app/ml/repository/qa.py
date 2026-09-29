"""예측 질의응답이 읽는 표의 SQL. **전부 SELECT 다.**

두 창고를 읽는다 — 어느 연결을 넘길지는 `readmodel/qa_reads.py` 가 정한다.

```text
서비스 창고  haetdeul.ml_price_forecasts   D+1~D+18 (달력일) · 매입 파트가 읽는 표
            latest_base_date · forecast_rows · usability
원본 창고    prediction_log                 채점 기록 · 리드 0(당일) · 지금 도는 모델
            latest_model_base_date · today_row · model_now
            batch_run · batch_run_stage    그날 배치와 실패한 단계
            agent_report                   그날 AI 점검 · 재학습 보고서
            model_cutover                  모델 교체 이력 (아직 없는 표일 수 있다)
```

★ **받은 연결로 SQL 만 실행한다.** 연결을 빌리지 않고 commit 하지 않는다.

🟢 **자리 (2026-09-29 · 재구성 BL-017).** 전에는 `app/ml/qa_tools.py` 한 파일에 SQL · 봉인
  성능표 · 라벨 · ML 백엔드 HTTP 호출이 함께 있었고, 실행은 `app/ml/db.py` 헬퍼가 SQL 마다
  연결을 빌려 했다. SQL 문면 · 매개변수는 그대로다. 상수는 `config.py`, 호출 순서와 연결은
  `readmodel/qa_reads.py`, ML 백엔드 호출은 `ml_backend.py` 에 있다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.core.db import Connection
from app.core.settings import get_db_schema

_LATEST_BASE_SQL = """
SELECT MAX(base_dt) AS base_dt
  FROM {schema}.ml_price_forecasts
 WHERE (%s::date IS NULL OR base_dt <= %s::date)
"""

_ROWS_SQL = """
SELECT base_dt, target_dt, offset_days, src_lead_biz_d,
       predicted, lower, upper, current_price, unit,
       is_filled, is_gated, gate_reason, band_method,
       use_recommended, quality_note,
       market_name, grade_name, spec_desc,
       model_version, generated_at
  FROM {schema}.ml_price_forecasts
 WHERE item_nm = %s AND target_kind = %s AND base_dt = %s
   AND target_dt = ANY(%s)
 ORDER BY offset_days
"""

#: 당일(리드 0). 원본 창고에는 있고 전달표에는 없다.
_TODAY_SQL = """
SELECT base_dt, target_dt, lead_biz_d,
       pred_prc AS predicted, pred_lo AS lower, pred_hi AS upper,
       anchor_prc AS current_price, unit, gated AS is_gated, gate_reason,
       band_method, model_ver AS model_version, model_created_at
  FROM prediction_log
 WHERE model_ver = %s AND item_nm = %s AND base_dt = %s AND lead_biz_d = 0
 LIMIT 1
"""

_LATEST_SOURCE_BASE_SQL = """
SELECT MAX(base_dt) AS base_dt FROM prediction_log WHERE model_ver = %s
"""

_USABILITY_SQL = """
SELECT use_recommended, quality_note, band_method, is_gated, gate_reason
  FROM {schema}.ml_price_forecasts
 WHERE item_nm = %s AND target_kind = %s
 ORDER BY base_dt DESC, offset_days
 LIMIT 1
"""

# ── 배치 결과 · 점검 보고서 ────────────────────────────────────────────
#
# 🔴 **시간대가 표마다 다르다** (2026-09-16 실측).
#
# ```text
# batch_run.started_at    timestamptz · UTC 로 앉아 있다  -> AT TIME ZONE 으로 돌린다
# agent_report.ran_at     timestamp   · 이미 한국 시간이다 -> 그대로 자른다
# ```
#
# 한국 09:00 이 UTC 자정이라(CLAUDE.md §9) **UTC 로 날짜를 자르면 하루가 밀린다.**
# 09:00 배치가 전날 것으로 세어진다.

_BATCH_RUN_SQL = """
SELECT run_id, started_at, finished_at, status, host, n_ok, n_fail, note
  FROM batch_run
 WHERE (started_at AT TIME ZONE 'Asia/Seoul')::date = %s
 ORDER BY started_at DESC
 LIMIT 1
"""

_FAILED_STAGE_SQL = """
SELECT seq, stage, ok, duration_s, message
  FROM batch_run_stage
 WHERE run_id = %s AND ok = false
 ORDER BY seq
"""

_REPORT_SQL = """
SELECT id, name, verdict, ran_at, payload, body
  FROM agent_report
 WHERE name = %s AND ran_at::date = %s
 ORDER BY ran_at DESC
 LIMIT 1
"""

#: 그날 같은 이름의 보고서 **전부**. 시간 오름차순 — 돈 순서가 읽는 순서다.
_REPORTS_SQL = """
SELECT id, name, verdict, ran_at, payload, body
  FROM agent_report
 WHERE name = %s AND ran_at::date = %s
 ORDER BY ran_at
"""

# ── 지금 무엇이 도나 ──────────────────────────────────────────────────
#
# 🔴 **이름으로는 알 수 없다.** `ops_auc` · `ops_whsl` · `ops_rtl` 은 모델을
#   갈아 끼워도 **그대로 둔다** — 매입 파트 필터가 이름 정확히 일치라 바꾸면
#   에러 없이 0건이 된다 (CLAUDE.md §5.11).
#
#   그래서 «현재 모델» 은 **이름 + 만든 날 + 학습 끝** 셋이 있어야 가려진다.
#   실제로 2026-09-15 저녁에 소매가 모델이 바뀌었는데, 이름이 같아 답에
#   그 사실이 한 글자도 안 남았다.

#: 지금 예측을 내고 있는 번들을 만든 시각. **최신 기준일 행에서 읽는다** —
#: 옛 기준일 행에는 교체 전 번들의 시각이 그대로 남아 있다 (실측: `ops_rtl` 이
#: 2026-09-15 까지 09-08 번들, 09-16 부터 09-12 번들).
_MODEL_NOW_SQL = """
SELECT model_ver, base_dt, model_created_at
  FROM prediction_log
 WHERE model_ver = %s
 ORDER BY base_dt DESC
 LIMIT 1
"""

#: 교체 이력. **아직 없는 표다** (2026-09-16 실측 · 두 창고 다 없음).
#: 다른 일꾼이 만들고 있어 칸 이름이 바뀔 수 있으므로 `*` 로 받아 키로 읽는다.
_CUTOVER_SQL = """
SELECT DISTINCT ON (kind) *
  FROM model_cutover
 ORDER BY kind, swapped_at DESC
"""


def _one(conn: Connection, query: str, params: tuple) -> dict[str, Any] | None:
    with conn.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchone()


def _all(conn: Connection, query: str, params: tuple | None = None) -> list[dict[str, Any]]:
    with conn.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()


def latest_base_date(conn: Connection, as_of: date | None) -> date | None:
    """전달표의 최신 기준일. `as_of` 를 주면 그 날 이하에서 고른다 (미래를 안 본다)."""
    row = _one(conn, _LATEST_BASE_SQL.format(schema=get_db_schema()), (as_of, as_of))
    return row["base_dt"] if row else None


def forecast_rows(
    conn: Connection, item: str, kind: str, base_dt: date, targets: list[date]
) -> list[dict[str, Any]]:
    """전달표에서 여러 대상일을 **한 번에** 읽는다. 날짜마다 묻지 않는다."""
    query = _ROWS_SQL.format(schema=get_db_schema())
    return _all(conn, query, (item, kind, base_dt, list(targets)))


def latest_model_base_date(conn: Connection, model: str) -> dict[str, Any] | None:
    """원본 창고에서 그 운영 번들의 최신 기준일 행 (`{"base_dt": …}`)."""
    return _one(conn, _LATEST_SOURCE_BASE_SQL, (model,))


def today_row(conn: Connection, model: str, item: str, base_dt: date) -> dict[str, Any] | None:
    """당일(리드 0) 한 행. **원본 창고** — 전달표에는 없는 값이다."""
    return _one(conn, _TODAY_SQL, (model, item, base_dt))


def usability(conn: Connection, item: str, kind: str) -> dict[str, Any] | None:
    """판단에 써도 되는 조합인가. `use_recommended=False` 면 쓰지 말라는 뜻이다."""
    return _one(conn, _USABILITY_SQL.format(schema=get_db_schema()), (item, kind))


def batch_run(conn: Connection, on: date) -> dict[str, Any] | None:
    """그날 배치 **한 행** (시작 시각 내림차순 첫 행)."""
    return _one(conn, _BATCH_RUN_SQL, (on,))


def failed_stages(conn: Connection, run_id: int) -> list[dict[str, Any]]:
    """그 실행에서 **실패한 단계만**."""
    return _all(conn, _FAILED_STAGE_SQL, (run_id,))


def agent_report(conn: Connection, name: str, on: date) -> dict[str, Any] | None:
    """그날 그 이름의 보고서 **한 건** (가장 나중 것)."""
    return _one(conn, _REPORT_SQL, (name, on))


def agent_reports(conn: Connection, name: str, on: date) -> list[dict[str, Any]]:
    """그날 그 이름의 보고서 **전부** (돈 순서)."""
    return _all(conn, _REPORTS_SQL, (name, on))


def model_now(conn: Connection, model_ver: str) -> dict[str, Any] | None:
    """그 운영 번들의 최신 기준일 행 — 이름 · 기준일 · 만든 시각."""
    return _one(conn, _MODEL_NOW_SQL, (model_ver,))


def cutover_rows(conn: Connection) -> list[dict[str, Any]]:
    """가격 종류마다 가장 최근 교체 한 행. 표가 없으면 `psycopg.errors.UndefinedTable`."""
    return _all(conn, _CUTOVER_SQL)
