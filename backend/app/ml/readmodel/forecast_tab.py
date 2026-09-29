"""가격 예측 탭(화면 `GET /api/forecast`)이 읽는 원본 창고 조회 — 조회 한 번에 연결 하나.

★ 네 조회가 **따로 실패할 수 있다.** 화면(`app/api/forecast/query.py::_fetch`)이 조회마다
  예외를 받아 «예시값» 으로 떨어뜨린다 — 그래서 한 연결로 묶지 않고 조회마다 원본 창고 풀에서
  빌리고 돌려준다(종전 `app/ml/db.py::fetch_all(source=True)` 와 같은 대여 단위).

🟢 **자리 (2026-09-29 · 재구성 BL-017).** 전에는 화면 파일이 SQL 을 들고 `app/ml/db.py` 로
  실행했다. SQL 은 `repository/forecast_tab.py`, 화면에는 표 · 차트 조립만 남았다.
"""

from __future__ import annotations

from typing import Any

from app.core import db as core_db
from app.ml.repository import forecast_tab as tab_sql


def base_dates(models: list[str], limit: int) -> list[dict[str, Any]]:
    """운영 모델이 예측을 낸 기준일 목록 (최근 것부터)."""
    with core_db.ML_SOURCE_POOL.read_connection() as conn:
        return tab_sql.base_dates(conn, models, limit)


def forecast_rows(models: list[str], base_dt: str, kind: str, item: str) -> list[dict[str, Any]]:
    """한 기준일 · 가격 종류 · 품목의 리드타임별 예측 · 실제값."""
    with core_db.ML_SOURCE_POOL.read_connection() as conn:
        return tab_sql.forecast_rows(conn, models, base_dt, kind, item)


def card_rows(
    models: list[str], base_dt: str, kind: str, items: list[str], lead: int
) -> list[dict[str, Any]]:
    """품목마다 기준일 그날 값 한 행 (카드 세 장)."""
    with core_db.ML_SOURCE_POOL.read_connection() as conn:
        return tab_sql.card_rows(conn, models, base_dt, kind, items, lead)


def quality_rows() -> list[dict[str, Any]]:
    """가격 종류 · 품목마다 «써도 되나» 진단."""
    with core_db.ML_SOURCE_POOL.read_connection() as conn:
        return tab_sql.quality_rows(conn)
