"""ML 예측 전달표의 SQL — 원본 창고에서 읽고 서비스 창고에 적재하고, 서비스 창고에서 읽는다.

```text
read_source_predictions · latest_source_base_date   원본 창고 prediction_log (개장일 축)
upsert_forecasts                                    서비스 창고 ml_price_forecasts 적재 (달력일 축)
latest_forecast_rows                                서비스 창고에서 as_of 이하 최신 기준일 행
```

★ **받은 연결로 SQL 만 실행한다.** 연결을 빌리지도 commit 하지도 않는다 — 어느 창고의
  연결을 빌릴지와 트랜잭션은 부르는 쪽(`service/forecasts.py` · `readmodel/forecasts.py`)이 정한다.

🟢 **자리 (2026-09-29 · 재구성 BL-017).** 전에는 `app/ml/repository.py`(원본 읽기 · 적재 ·
  개장일 → 달력일 변환)와 `app/ml/service.py`(`_READ_SQL`)에 나뉘어 있었고, 실행은
  `app/ml/db.py` 헬퍼가 SQL 마다 연결을 빌려 했다. SQL 문면 · 매개변수는 그대로다.
  변환은 `domain/forecast_calendar.py` 로 갔다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from app.core.db import Connection
from app.core.settings import get_db_schema
from app.ml.config import OPS_MODELS

_SOURCE_SQL = """
SELECT p.base_dt, p.target_dt, p.item_nm, p.lead_biz_d, p.target_kind, p.unit,
       p.anchor_prc, p.pred_prc, p.pred_lo, p.pred_hi, p.gated, p.gate_reason,
       p.model_ver, p.model_created_at,
       q.use_recommended, q.note AS quality_note
  FROM prediction_log p
  LEFT JOIN ref_prediction_quality q
         ON q.target_kind = p.target_kind AND q.item_nm = p.item_nm
 WHERE p.base_dt = %s
   AND p.model_ver = ANY(%s)
   AND p.item_nm = ANY(%s)
 ORDER BY p.target_kind, p.item_nm, p.lead_biz_d
"""

_LATEST_SQL = """
SELECT MAX(base_dt) AS base_dt FROM prediction_log WHERE model_ver = ANY(%s)
"""

_UPSERT_SQL = """
INSERT INTO {schema}.ml_price_forecasts
 (base_dt, item_nm, target_kind, offset_days, target_dt,
  predicted, lower, upper, current_price, unit, model_version, generated_at,
  src_lead_biz_d, is_filled, is_gated, gate_reason,
  market_name, grade_name, spec_desc, unit_weight_kg, quality_note, use_recommended)
VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s,%s,%s)
ON CONFLICT (base_dt, item_nm, target_kind, offset_days) DO UPDATE SET
  target_dt=EXCLUDED.target_dt, predicted=EXCLUDED.predicted,
  lower=EXCLUDED.lower, upper=EXCLUDED.upper,
  current_price=EXCLUDED.current_price, unit=EXCLUDED.unit,
  model_version=EXCLUDED.model_version, generated_at=EXCLUDED.generated_at,
  src_lead_biz_d=EXCLUDED.src_lead_biz_d, is_filled=EXCLUDED.is_filled,
  is_gated=EXCLUDED.is_gated, gate_reason=EXCLUDED.gate_reason,
  market_name=EXCLUDED.market_name, grade_name=EXCLUDED.grade_name,
  spec_desc=EXCLUDED.spec_desc, unit_weight_kg=EXCLUDED.unit_weight_kg,
  quality_note=EXCLUDED.quality_note, use_recommended=EXCLUDED.use_recommended
"""

_READ_SQL = """
SELECT base_dt, item_nm, target_kind, offset_days, target_dt,
       predicted, lower, upper, current_price, unit,
       model_version, generated_at, is_filled, is_gated,
       market_name, grade_name, spec_desc, quality_note, use_recommended
  FROM {schema}.ml_price_forecasts
 WHERE item_nm = %s AND target_kind = %s AND base_dt <= %s
   AND base_dt = (SELECT MAX(base_dt) FROM {schema}.ml_price_forecasts
                   WHERE item_nm = %s AND target_kind = %s AND base_dt <= %s)
 ORDER BY offset_days
"""


def latest_source_base_date(conn: Connection) -> date | None:
    """원본 창고에서 예측이 있는 가장 최근 기준일."""
    with conn.cursor() as cursor:
        cursor.execute(_LATEST_SQL, (list(OPS_MODELS),))
        rows = cursor.fetchall()
    return rows[0]["base_dt"] if rows else None


def read_source_predictions(
    conn: Connection, base_dt: date, items: tuple[str, ...]
) -> list[dict[str, Any]]:
    """원본 창고의 개장일 기준 예측을 읽는다."""
    with conn.cursor() as cursor:
        cursor.execute(_SOURCE_SQL, (base_dt, list(OPS_MODELS), list(items)))
        return cursor.fetchall()


def upsert_forecasts(conn: Connection, rows: Sequence[Sequence[object]], schema: str) -> int:
    """서비스 창고에 적재한다. 같은 기준일을 다시 넣으면 덮어쓴다. 적재한 행 수를 돌려준다.

    ★ commit 하지 않는다 — 적재 한 번 = 트랜잭션 하나를 부르는 쪽이 눈에 보이게 연다.
    ★ `schema` 를 인자로 받는다. 부르는 쪽이 연결을 빌리기 **전에** 스키마 이름을 읽는
      종전 순서(`DB_SCHEMA` 가 없으면 적재할 행이 없어도 멈춘다)를 그대로 두기 위해서다.
    """
    with conn.cursor() as cursor:
        cursor.executemany(_UPSERT_SQL.format(schema=schema), rows)
    return len(rows)


def latest_forecast_rows(
    conn: Connection, *, item: str, target_kind: str, as_of: date
) -> list[dict[str, Any]]:
    """서비스 창고에서 ``as_of`` **이하** 의 가장 최근 기준일 행을 대상일 차례로."""
    query = _READ_SQL.format(schema=get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(query, (item, target_kind, as_of, item, target_kind, as_of))
        return cursor.fetchall()
