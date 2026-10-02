"""가격 예측 탭(화면 `GET /api/forecast`)의 SQL — 원본 창고(`prediction_log`)를 읽는다.

서비스 창고(`haetdeul.ml_price_forecasts`)에는 실제값 · 채점이 없다. «얼마나 틀렸나» 를
보이려면 채점이 있어야 해서 원본 창고를 읽는다(화면 `app/api/forecast/presenter.py` 머리말).

받은 연결로 SQL 만 실행한다. 연결은 `readmodel/forecast_tab.py` 가 원본 창고 풀에서 빌린다.
모델 이름 · 품목 · 상한 같은 거르기 값은 화면이 넘긴 그대로 쓴다.
"""

from __future__ import annotations

from typing import Any

from app.core.db import Connection

_SQL_BASE_DATES = """
    SELECT base_dt, COUNT(*) AS n, COUNT(actual_prc) AS scored,
           MIN(created_at) AS made_at
      FROM prediction_log
     WHERE model_ver = ANY(%s)
     GROUP BY base_dt
     ORDER BY base_dt DESC
     LIMIT %s
"""

_SQL_ROWS = """
    SELECT lead_biz_d, target_dt, anchor_prc, pred_prc, pred_lo, pred_hi,
           actual_prc, abs_pct_err, gated, gate_reason
      FROM prediction_log
     WHERE model_ver = ANY(%s) AND base_dt = %s AND target_kind = %s AND item_nm = %s
     ORDER BY lead_biz_d
"""

#: 세 품목의 기준일 그날 값(리드 0).
#:
#: 리드 1~2 를 덮는 게이트를 끄고 리드 0 을 만들었으므로 카드는 기준일 그날 값을 보인다.
#: «게이트를 지난 첫 리드» 를 쓰면 기준일보다 며칠 뒤의 값이 카드에 뜬다.
#:
#: 그날 값이 없으면 가장 가까운 리드로 떨어진다(`ORDER BY lead_biz_d`).
_SQL_CARDS = """
    SELECT DISTINCT ON (item_nm)
           item_nm, lead_biz_d, target_dt, pred_prc, pred_lo, pred_hi, gated
      FROM prediction_log
     WHERE model_ver = ANY(%s) AND base_dt = %s AND target_kind = %s
       AND item_nm = ANY(%s) AND lead_biz_d >= %s
     ORDER BY item_nm, lead_biz_d
"""

_SQL_QUALITY = """
    SELECT target_kind, item_nm, use_recommended, note
      FROM ref_prediction_quality
     ORDER BY target_kind, item_nm
"""


def _rows(conn: Connection, query: str, params: tuple) -> list[dict[str, Any]]:
    with conn.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()


def base_dates(conn: Connection, models: list[str], limit: int) -> list[dict[str, Any]]:
    """운영 모델이 예측을 낸 기준일 목록 (최근 것부터 ``limit`` 개) · 행 수 · 채점 수."""
    return _rows(conn, _SQL_BASE_DATES, (models, limit))


def forecast_rows(
    conn: Connection, models: list[str], base_dt: str, kind: str, item: str
) -> list[dict[str, Any]]:
    """한 기준일 · 가격 종류 · 품목의 리드타임별 예측 · 실제값."""
    return _rows(conn, _SQL_ROWS, (models, base_dt, kind, item))


def card_rows(
    conn: Connection,
    models: list[str],
    base_dt: str,
    kind: str,
    items: list[str],
    lead: int,
) -> list[dict[str, Any]]:
    """품목마다 기준일 그날(리드 ``lead`` 이상 가장 가까운) 값 한 행."""
    return _rows(conn, _SQL_CARDS, (models, base_dt, kind, items, lead))


def quality_rows(conn: Connection) -> list[dict[str, Any]]:
    """가격 종류 · 품목마다 «써도 되나» 진단."""
    return _rows(conn, _SQL_QUALITY, ())
