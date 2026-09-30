"""시세 SQL — ``auction_prices_daily`` 에서 **as_of 이전 최신 거래일 하루**의 등급별 물량가중
집계.

🔴 **쓰기 SQL 이 없다** (규칙 2 — 매입은 read-only). 넘겨받은 조회 연결로 실행만 하고
  commit · rollback · 반환을 하지 않는다. 연결은 `readmodel/quotes.py` 가 빌린다.

🔴 ``subclass_*``(품종)는 WHERE 에도 GROUP BY 에도 **없다** — 계약 검사가 이 파일의 원문을 본다
  (`test_auction_quotes.py`). 이유는 `readmodel/quotes.py` 머리말 «품종을 고르지 않는다».

🟢 **자리 (2026-09-29 · 재구성 BL-016).** 전에는 `quotes.py` 의 `_weight_condition` · `_query` 와
  `db.fetch_all` 이었다. SQL 문면은 그대로이고, 시세 공급자가 부르므로 공개
  이름(`weight_condition` · `auction_query`)으로 올렸다.
"""

from collections.abc import Mapping
from typing import Any

from psycopg import sql

from app.core.db import Connection, Params, Query


def weight_condition(spec: Mapping[str, Any]) -> sql.Composable:
    """규격 중량 조건. **날짜에 따라 바뀌는 품목이 있다.**

    무는 2018-01-01 이전이 18kg 이다 (ML ``spec_desc``: *"상자·파렛트 20kg (2018년 이전
    18kg)"*). 우리 IO명세도 *"무 규격 2018 전 18kg(백테스트 주의)"* 로 적어두고 있었는데
    코드에는 없었다 — 20kg 로 고정하면 2017년 244 거래일 중 **64일만** 잡힌다(18kg 는
    227일). 에러 없이 다른 시리즈가 된다.

    ``CASE`` 로 쓰는 이유: 두 중량을 ``IN`` 으로 합치면 전환 전후가 한 날에 섞일 수 있다.
    2017년에 18kg 과 20kg 이 **둘 다 존재**해서 실제로 섞인다.
    """
    before = spec.get("before")
    if before is None:
        return sql.SQL("unit_weight_kg = %(unit_weight_kg)s")
    return sql.SQL(
        "unit_weight_kg = CASE WHEN auction_date < %(spec_switch_date)s "
        "THEN %(unit_weight_before)s ELSE %(unit_weight_kg)s END"
    )


def auction_query(schema: str, table: str, weight_condition: sql.Composable) -> sql.Composed:
    """**as_of 이전 최신 거래일 하루**의 등급별 물량가중 집계.

    🔴 ``auction_date < as_of`` 다 — 당일이 아니다 (2026-08-31 확인).
      우리는 **아침에 돈다**(상세설계 §128 · 역할계약서 §45 · 백로그 §12 · CLAUDE.md).
      그 시각엔 당일 경매가 끝나지 않았고, 적재는 더 늦다 — 2026-08-31 실측으로
      ``haetdeul`` 최신이 08-26(5일 지연), 원천도 08-29(2일 지연)였다.
      ``= as_of`` 로 두면 실운영에서 **매일 0행**이고, 그때마다 "휴장이거나 그 규격 거래가
      없다"는 **틀린 사유**가 나간다. 12-31 관통이 돌았던 건 그날이 9개월 전 과거라
      이미 적재돼 있었기 때문이지, 실운영 가능성을 보인 게 아니다.

    🔴 **하루를 통째로 고른다 — 등급별로 각자 거슬러 올라가지 않는다.**
      등급마다 "그 등급의 최신 거래일"을 잡으면 2025-12-31 기준으로 이렇게 된다::

          배추 특  2025-12-30 (1일 전)
          배추 상  2025-11-12 (1.5개월 전)
          배추 하  2023-02-23 (2.8년 전)

      스프레드가 **서로 다른 시점의 두 가격**을 비교하게 되고, 규칙 4의 "당일 시세에
      실재하는 값"이 무너진다. ``max(auction_date)`` 를 먼저 정하고 그날 것만 쓴다.

    🔴 ``subclass_*`` 가 **없다** — WHERE 에도 GROUP BY 에도. 품종을 고르지 않는다는
      결정이 코드에서 지켜지는 자리이고, 계약 테스트가 이 문자열을 검사한다.

    🔴 **행 단위로 거른다 — 집계 뒤에 거르면 늦다** (Codex 교차검증 2026-08-31).
      ``sum()`` 은 NULL 을 **건너뛰는데** 다른 컬럼의 합계에는 그 행의 값이 그대로 들어간다.
      그래서 집계 결과만 보고 막으면 분자·분모가 서로 다른 행 집합에서 나온다::

          (1000, 10) + (NULL, 10)  →  sum 1000 / 20 =  50   (정답 100)
          (1000, 10) + (1000,  0)  →  sum 2000 / 10 = 200   (정답 100)

      두 경우 다 총중량이 양수라 ``HAVING`` 을 통과하고, **에러 없이 단가가 2배 틀린다.**
      한번 합쳐진 뒤에는 복구할 방법이 없으므로 필터가 여기 있어야 한다.

      ``trade_volume_kg > 0`` 은 NULL 도 함께 떨어뜨린다(NULL 비교는 참이 아니다).
      금액만 ``IS NOT NULL`` 을 따로 적는다 — 0원 낙찰은 있을 수 있어 ``> 0`` 이 아니다.

    🔄 ``HAVING`` 이 **생겼다** (`#559` · 2026-09-11). 전에는 *"남은 행이 전부 양수
    중량이라 그룹 합계도 양수이고, 같은 뜻의 검사를 두 곳에 두면 한쪽만 바뀐다"* 로
    두지 않았다. **그 문장은 여전히 맞고, 지금 것은 같은 뜻이 아니다.**

    .. code-block:: text

        행 단위 trade_volume_kg > 0    물량가중의 **분모를 지킨다** (위 두 줄의 예시)
        HAVING  sum >= 하한            *"이 값을 시세라고 부를 수 있나"*

    🔴 **없으면 한두 망이 그날 등급 단가가 된다.** 실측 (2025-09-19 배추)::

        상 그물망        10 kg   3,750 원/kg   ← 이것이 「그날 상 시세」였다
        특 그물망   382,960 kg   1,026 원/kg

    그 단가가 ``grade_unit_price`` 로 안에 실리고, 그건 사중 일치 금액 축이다 (규칙 4).
    그리고 배추 ``특/상`` 스프레드 12개월 중앙이 ``-2.466`` 이 되어 ⑤ 진입 게이트가
    뒤집힌다 — 값이 아니라 **부호**가 틀린다.

    ★ **``picked`` 에는 안 건다.** 하한을 ``usable`` 에 걸면 관측일 선택까지 바뀐다 —
      그날 얇은 등급만 있었어도 **그날은 그날**이고, 거르는 것은 등급이지 날짜가 아니다.
      (실측으로 이 하한에 **등급이 0개가 되는 날은 12개월·세 품목 전부 0일**이다.)

    🔴 ``market`` CTE 에는 **품목 필터가 없다 — 없는 것이 맞다.**
      이 CTE 가 답하는 질문은 *"시장이 언제 열렸나"* 이지 *"우리 품목이 언제 팔렸나"* 가
      아니다. 품목을 걸면 ``market`` 이 ``usable`` 과 같은 날짜 집합이 되어
      ``trading_days_behind`` 가 **항상 0**이 되고, 검사가 있는데 아무것도 안 보는 상태가
      된다 (규칙 8). 계약 테스트가 이 필터 부재를 잠근다.

      필터가 없어야 두 상황이 갈린다::

          시장 개장 + 우리 규격 거래 있음  → behind 0        정상
          시장 개장 + 우리 규격 거래 없음  → behind N        "이 규격이 안 팔렸다"
          시장 휴장                        → behind 0        연휴는 지연이 아니다

      **적재가 멈춘 경우는 여기서 안 잡힌다.** 시장 최신일이 우리 관측일과 같이 뒤로
      밀려 ``behind`` 가 0으로 읽히기 때문이다. 그 사각은 ``market_last_open`` 을 달력일로
      재는 ``max_calendar_days_behind`` 가 맡는다.

    ⚠️ ``market`` 을 창으로 제한하는 이유는 성능이다. ``source_raw`` 는 FOREIGN 테이블이라
      경계 없는 ``max(auction_date)`` 한 번에 원격 커넥션이 끊긴다 (2026-08-31 실측).
    """
    return sql.SQL("""
        WITH usable AS (
          SELECT auction_date, grade_name, trade_amount_krw, trade_volume_kg
            FROM {schema}.{table}
           WHERE auction_date     <  %(as_of)s
             AND market_category  =  %(market_category)s
             AND item_name        =  %(item)s
             AND grade_name       =  ANY(%(grades)s)
             AND package_name     =  ANY(%(packages)s)
             AND {weight}
             AND trade_volume_kg  >  0
             AND trade_amount_krw IS NOT NULL
             AND trade_amount_krw >= 0
        ),
        picked AS (SELECT max(auction_date) AS day FROM usable),
        market AS (
          SELECT DISTINCT auction_date AS day
            FROM {schema}.{table}
           WHERE auction_date    <  %(as_of)s
             AND auction_date    >= %(as_of)s::date - %(market_window_days)s
             AND market_category =  %(market_category)s
        )
        SELECT usable.auction_date AS observed_at,
               usable.grade_name   AS grade,
               sum(usable.trade_amount_krw) AS amount_krw,
               sum(usable.trade_volume_kg)  AS volume_kg,
               (SELECT max(day) FROM market) AS market_last_open,
               (SELECT count(*) FROM market
                 WHERE market.day > (SELECT day FROM picked)) AS trading_days_behind
          FROM usable
         WHERE usable.auction_date = (SELECT day FROM picked)
         GROUP BY usable.auction_date, usable.grade_name
        HAVING sum(usable.trade_volume_kg) >= %(min_trade_volume_kg)s
    """).format(
        schema=sql.Identifier(schema), table=sql.Identifier(table), weight=weight_condition
    )


def fetch_rows(conn: Connection, query: Query, params: Params = None) -> list[dict[str, Any]]:
    """받은 조회 연결로 집계 SQL 을 실행하고 행을 돌려준다. **빌리지도 commit 하지도 않는다.**"""
    with conn.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()
