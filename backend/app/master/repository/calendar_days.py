"""`ml_calendar_days` 읽기 SQL — 공휴일 · 장 서는 날 · 예측 배치일 (받은 연결, 읽기만).

★ 2026-09-30 재구성 BL-018: 세 달력 모듈(`master/holiday_calendar.py` · `market_calendar.py` ·
  `ml_batch_calendar.py`)의 `_read_table` 안 SQL 을 옮겼다. 같은 표지만 **칸이 다른 SELECT 셋을
  합치지 않는다** — 달력마다 자기 칸만 읽고 따로 캐시한다(합치면 한 달력의 판정에 다른 칸이
  끼어들 자리가 생기고, 읽기 횟수도 바뀐다). 조회 연결은 각 `readmodel/*_calendar.py` 가 빌린다.
"""

from __future__ import annotations

from typing import Any

from psycopg import sql

#: ML 이 소유하는 **표**. 공휴일 · 개장 · 배치의 주인이다. **읽기만 한다.**
TABLE = "ml_calendar_days"

Rows = list[dict[str, Any]]


def _fetch(conn: Any, query: sql.Composed) -> Rows:
    with conn.cursor() as cursor:
        cursor.execute(query)
        return cursor.fetchall()


def select_holiday_rows(conn: Any, *, schema: str) -> Rows:
    """표를 통째로 한 번 읽는다. **날짜와 공휴일 이름만** 가져온다.

    ★ `SELECT *` 를 안 쓴다. 다른 칸을 안 가져오면 **나중에 누가 그 칸으로 판정하는
      일이 생기지 않는다** — `is_open` 으로 판정하지 않겠다는 결정(§A)을 조회 모양이
      거들게 한다. 표에는 `is_survey` 도 있으므로 그 규율이 더 필요하다.

    ★ 한 번에 다 읽는다. 실측 732행이라 날짜마다 묻는 것보다 싸고, 무엇보다
      **덮는 범위를 알 수 있다** — 범위를 모르면 *"이 날이 없다"* 를 사유로 못 적는다.
    """
    query = sql.SQL("SELECT dt, holiday_nm FROM {}.{} ORDER BY dt").format(
        sql.Identifier(schema), sql.Identifier(TABLE)
    )
    return _fetch(conn, query)


def select_market_rows(conn: Any, *, schema: str) -> Rows:
    """표를 통째로 한 번 읽는다. **날짜와 개장 여부만** 가져온다.

    ★ 표 이름은 이 파일의 `TABLE` 하나다. 두 모듈이 같은 표를 읽으므로
      **ML 이 표 이름을 바꾸면 한 자리만 고치면 된다.**
    """
    query = sql.SQL("SELECT dt, is_open FROM {}.{} ORDER BY dt").format(
        sql.Identifier(schema), sql.Identifier(TABLE)
    )
    return _fetch(conn, query)


def select_batch_rows(conn: Any, *, schema: str) -> Rows:
    """표를 통째로 한 번 읽는다. **날짜와 배치 여부만** 가져온다.

    ★ 표 이름은 이 파일의 `TABLE` 하나다. 세 모듈이 같은 표를 읽으므로
      **ML 이 표 이름을 바꾸면 한 자리만 고치면 된다.**
    """
    query = sql.SQL("SELECT dt, is_survey FROM {}.{} ORDER BY dt").format(
        sql.Identifier(schema), sql.Identifier(TABLE)
    )
    return _fetch(conn, query)
