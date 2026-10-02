"""ML 예측 적재 — 원본 창고 → 서비스 창고.

    push_forecasts()  원본 창고 -> 서비스 창고. 배치가 하루 한 번 부른다

앱 안에서 부르는 곳이 없다. `POST /ml/forecast/push` 는 주석 처리되어 있고, 적재는 ML
배치가 `연동/push_forecast.py` 로 직접 한다(`app/api/ml/qa.py` 머리말). 지울지는 아직 정하지
않았다(설계 쟁점 4, 죽은 코드 범위).

순서와 연결

```text
원본 창고 조회 연결 하나   최신 기준일(주지 않았으면) → 개장일 예측 읽기
(연결 없음)               개장일 → 달력일 변환 (domain/forecast_calendar.py) · 스키마 이름 읽기
서비스 창고 연결 하나      적재 → commit 1회   ← 적재할 행이 없으면 빌리지 않는다
```

원본 창고는 한 번 빌려 두 SQL(최신 기준일 · 예측 읽기)을 보낸다. 계약 모양 조회는
`readmodel/forecasts.py::get_forecast` 다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.core import db as core_db
from app.core.settings import get_db_schema
from app.ml.domain.forecast_calendar import to_calendar_rows
from app.ml.repository import forecasts as forecasts_sql
from app.ml.schemas.forecast import ITEMS


def push_forecasts(base_dt: date | None = None, items: tuple[str, ...] = ITEMS) -> dict[str, Any]:
    """원본 창고의 예측을 서비스 창고로 옮긴다.

    같은 기준일을 다시 부르면 덮어쓴다. 배치가 여러 번 돌아도 안전하다.
    """
    with core_db.ML_SOURCE_POOL.read_connection() as conn:
        if base_dt is None:
            base_dt = forecasts_sql.latest_source_base_date(conn)
        if base_dt is None:
            raise RuntimeError("원본 창고에 예측이 없습니다. 배치가 돌았는지 확인하세요.")
        source = forecasts_sql.read_source_predictions(conn, base_dt, items)
    if not source:
        raise RuntimeError(f"{base_dt} 예측이 없습니다.")

    rows = to_calendar_rows(source, base_dt)
    schema = get_db_schema()
    n = 0
    if rows:
        #   적재 한 번 = 트랜잭션 하나. commit 을 눈에 보이게 적는다.
        with core_db.connection() as conn:
            n = forecasts_sql.upsert_forecasts(conn, rows, schema)
            conn.commit()
    filled = sum(1 for r in rows if r[13])
    return {
        "base_dt": base_dt.isoformat(),
        "source_rows": len(source),
        "loaded_rows": n,
        "filled_rows": filled,
        "items": sorted({r[1] for r in rows}),
    }
