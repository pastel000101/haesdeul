"""가락 등급별 당일 경락가를 ``auction_prices_daily`` 에서 읽는다 (#70).

시세는 매입 자기 도메인이다 (정의서 §4.1). 마스터 봉투로 받지 않고 우리가 직접 읽는다.
읽기 경로는 이 모듈 하나뿐이고, 공통 풀에서 조회 전용 연결만 빌린다 (규칙 2 — 아래
«읽기 전용»).

역할: 여기서는 좌표를 읽고 조회 연결을 빌려 SQL 을 부른 뒤 결과를 시세 모양으로 만든다.
SQL 은 `repository/quotes.py`, 판정은 `domain/quotes.py`, 연결 · 타임아웃은
`app/core/db.py` 에 있다.

## 좌표 여섯을 왜 여기서 안 정하는가

전부 ``constraints.yaml`` 의 ``market_quotes`` 절에서 읽는다 (규칙 7). 이 모듈은 그 좌표로
쿼리를 만들 뿐이다. ML 이 2026-08-27 에 규격을 한 번 바꿨고, 바뀌면 고칠 자리가 한 곳이어야
한다.

## 품종을 고르지 않는다

``subclass_code`` · ``subclass_name`` 은 ``WHERE`` 에도 ``GROUP BY`` 에도 쓰지 않는다
(8/28 지환님 결정). 물량가중이 품종 축을 자동으로 정리하고, ML 수집·학습표와 같은 식이라
축이 일치한다. 하나라도 쓰는 순간 "고르지 않는다"가 깨지므로 계약 테스트가 쿼리
문자열(`repository/quotes.py`)을 검사한다.

다만 품종 정리는 규격을 잠근 뒤에야 성립한다. 2025-12-31 배추 그물망 10kg 은
김장(가을)배추 단일인데, 규격을 풀면 쌈배추(12,484원/kg)가 같은 가중에 들어와 축이 무너진다.

## 이 시리즈는 ML ``current_price`` 와 같지 않다 — 그리고 같으면 안 된다

같은 좌표(서울가락 · 특 · 그물망·파렛트 10kg)로 계산해도 2025-12-31 배추가
우리 933원 vs ML 812원 이다. 그 칸은 시세가 아니라 모델이 출발점으로 쓴 앵커다 (ML 회신)::

    anchor = 0.4 × 어제 실제값 + 0.6 × 최근 7 거래일 평균
             경락(AUC) 의 α = 0.4 · 중도매 0.8 · 소매 1.0 (섞지 않음)

    2025-12-31 배추   0.4 × 824.0 + 0.6 × 803.59 = 811.76   ← ML 이 그날 보낸 812

실 DB 재현 (2026-09-10 · 3품목 × 170 기준일 = 510 조합 중 491 일치). 어긋난 19는 ML 이
규격을 바꾼 08-21~08-27 구간과 09-04 · 09-07 이다.

어제값을 섞기 때문에 어떤 「후행 N일 평균」으로도 안 나온다. 그리고 그 7 거래일에는
토요일이 든다. 영업일로 세면 틀린다: 09-05(토) 626.89 를 빼면 09-09 배추 앵커가 704.69
대신 728.40 이 된다.

그래서 ``rise_rate`` 분모는 ML 이 예측과 같은 행에 동봉한 ``current_price`` 를 그대로
쓴다 (#57). 이유: ``predicted`` 가 그 앵커에서 출발하므로 같은 기준선끼리 비교하는 것이
옳다. 당일 시세를 분모로 넣으면 두 시리즈가 섞여 배추 기준 rise_rate 가 12.2%p 갈린다.

## 읽기 전용 (규칙 2)

쓰기 대여를 들이지 않는다. 공통 연결 모듈(`app/core/db.py`)에서 조회 전용 대여
(`read_connection`) 하나만 이름으로 가져온다. 모듈을 통째로 들이면 쓰기 대여(`connection`)와
쓰기 경계(`transaction`)까지 이 이름공간에 따라 들어와, "없으면 import 에서 막힌다" 가
약해진다. SQL 은 `repository/quotes.py` 가 받은 연결로 실행만 한다 — 이 사실은 계약 검사가
잠근다 (`test_auction_quotes.py`).

스키마 이름을 환경변수에서 읽지 않는다 (`get_db_schema` 를 쓰지 않는다). 읽을 스키마는
``constraints.yaml`` 의 ``market_quotes.source`` 가 정한다 (``source_table``). 환경변수가
정하게 두면 ``DB_SCHEMA`` 가 ``haetdeul`` 인 머신은 3일 된 사본을 보게 된다.

접속 정보는 다른 파트와 같은 ``DB_*`` 환경변수(공통 서비스 풀)를 쓴다. 이건 접속 정보이지
"mock 이냐 DB 냐"의 스위치가 아니다 — 그 선택은 명시 주입이다
(``ports.get_market_quotes(source=...)``).
"""

from collections.abc import Callable, Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from app.core.db import Params, Query, read_connection
from app.purchase_agent.config import load_constraints
from app.purchase_agent.domain.quotes import (
    iso_or_none,
    krw_per_kg,
    min_trade_volume_kg,
    source_table,
    spec_for_item,
    spec_label_on,
    to_price,
    validate_coordinates,
)
from app.purchase_agent.repository.quotes import auction_query, fetch_rows, weight_condition

#: 시세 공급자. ``ports.get_market_quotes`` 가 이 모양의 콜러블을 주입받는다.
#: mock 과 DB 를 명시 주입으로 고른다 — 환경변수 스위치를 두지 않는다.
QuoteSource = Callable[[str, date], list[dict[str, Any]]]

#: DB 조회 함수. 테스트가 가짜 행을 꽂을 수 있게 인자로 빼둔다.
Fetch = Callable[..., list[dict[str, Any]]]


def _amount(value: Any) -> Decimal | None:
    """집계 값 하나를 Decimal 로. 읽을 수 없으면 None 이다 — 0이 아니다 (규칙 3).

    ``auction_query`` 의 행 필터와 이중이다. 둘 다 두는 이유:

    둘은 다른 것을 막는다. ``auction_query`` 는 *어느 행을 합칠지*를 정하고 — 그게 유일하게
    올바른 자리다(합쳐진 뒤에는 복구가 불가능하다) — 이 함수는 *``_materialize`` 가 받은
    값이 숫자인지*를 본다. ``_materialize`` 는 쿼리 결과만 받는 게 아니다: 테스트가 가짜
    ``fetch`` 를 꽂고, 나중에 쿼리가 바뀔 수도 있다. 입력을 안 보는 순수 함수는 그때
    ``Decimal(None)`` 으로 죽는데, 죽는 쪽은 사유를 못 낸다 (결정 d).

    그러니 이 함수를 지우려면 ``_materialize`` 가 ``auction_query`` 전용이라는 보장이 먼저 있어야
    한다. 지금은 없다.

    2026-08-31 실측으로 가락 164,046행에 NULL·음수·0중량이 하나도 없다 — 두 방어 모두
    현재 데이터에서는 한 번도 발동하지 않는다. 그래도 두는 이유는 이 값들이 우리가 만드는
    게 아니라 적재되는 것이기 때문이다.
    """
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        return None
    converted = Decimal(value)
    return converted if converted.is_finite() and converted >= 0 else None


def _read_rows(query: Query, params: Params = None) -> list[dict[str, Any]]:
    """기본 조회 — 공통 서비스 풀에서 조회 전용 연결을 빌려 SQL 을 부르고 돌려준다.

    조회 한 번에 연결 하나, commit 없음(autocommit).
    """
    with read_connection() as conn:
        return fetch_rows(conn, query, params)


def auction_quote_source(*, fetch: Fetch | None = None) -> QuoteSource:
    """``ports.get_market_quotes`` 에 꽂을 DB 공급자를 만든다.

    ``fetch`` 를 인자로 뺀 이유는 테스트다 — 가짜 행을 꽂으면 쿼리 결과를 시세 형태로
    옮기는 부분(가중·반올림·정렬·등급 어휘)이 DB 없이 전부 시험된다. 실제 조회가 필요한
    테스트만 ``db`` 마커로 분리한다.

    ``schema`` 인자를 두지 않는다. 스키마는 선언(``constraints.yaml``)이 정하는데 인자로도
    덮을 수 있으면 "어느 테이블을 읽는가"의 답이 둘이 된다 — 한쪽만 바뀌는 자리를 만드는
    셈이다.
    """
    do_fetch = fetch if fetch is not None else _read_rows

    def load(item: str, as_of: date) -> list[dict[str, Any]]:
        # 좌표는 매 호출마다 다시 읽는다 — ``load_constraints`` 가 캐시하지 않는 이유와 같다
        # (feedback 이 임계를 덮어쓸 수 있어 공유 dict 를 들고 있으면 오염이 샌다).
        constraints = load_constraints()
        cfg = constraints["market_quotes"]
        validate_coordinates(cfg)
        spec = spec_for_item(item, constraints)
        if spec is None:
            # 규격이 ``null`` 로 선언된 품목은 조회하지 않는다. 아무 규격으로나
            # 물어보면 값이 오고, 그 값은 우리가 뜻한 시리즈가 아니다 (규칙 3).
            # 지금 선언에는 그런 품목이 없다 — 분기는 남긴다. ``spec_for_item``
            #    docstring 의 셋 중 둘째 자리다.
            return []
        params: dict[str, Any] = {
            "as_of": as_of,
            "market_category": cfg["market_category"],
            "item": item,
            "grades": list(cfg["grades"]),
            "packages": list(spec["packages"]),
            "unit_weight_kg": spec["unit_weight_kg"],
            "market_window_days": cfg["market_open_window_days"],
            "min_trade_volume_kg": min_trade_volume_kg(cfg),
        }
        before = spec.get("before")
        if before is not None:
            params["spec_switch_date"] = before["date"]
            params["unit_weight_before"] = before["unit_weight_kg"]
        schema, table = source_table(cfg)
        rows = do_fetch(auction_query(schema, table, weight_condition(spec)), params)
        return _materialize(rows, cfg, spec)

    return load


def _materialize(
    rows: list[dict[str, Any]], cfg: Mapping[str, Any], spec: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """집계 행을 IO명세 §1-② 의 시세 형태로 옮긴다.

    ``spec`` 을 한 줄씩 얹는다. 사유 문장이 "그날 그 규격에 그 등급이 없었다" 를 말하려면
    무엇을 보고 있었는지가 데이터에서 나와야 한다 — constraints 에서 다시 읽으면 mock 경로에
    없는 규격을 있는 것처럼 적게 된다.

    ``check_prices_exist`` 는 ``(market, grade, price)`` 세 키만 보므로 이 키는 대조를
    바꾸지 않고, ``materialize_sourcing`` 이 계약 4필드만 투영하므로 출력에도 새지 않는다.
    """
    # 등급별로 합산한다 — 행 하나를 집지 않는다. 지금 쿼리는 ``GROUP BY grade_name``
    #   이라 등급당 한 행이지만, ``{등급: 행}`` 으로 받으면 행이 둘 이상 오는 순간 마지막
    #   것만 조용히 남는다. 관통일 배추가 정확히 그 모양이다(그물망 10kg + 파렛트 10kg):
    #   합산이면 933원, 마지막 행만 집으면 970원 — 에러 없이 4% 어긋난다.
    #   여기서 합쳐두면 나중에 GROUP BY 축이 늘어도 물량가중이 유일한 정답으로 남는다.
    observed = _single_observed_at(rows)
    label = spec_label_on(spec, observed)
    # 시장 쪽 두 값은 행마다 같다(스칼라 서브쿼리) — 첫 행에서 한 번만 읽는다.
    market_last_open = iso_or_none(rows[0].get("market_last_open")) if rows else None
    behind = int(rows[0]["trading_days_behind"]) if rows else None
    totals: dict[str, list[Decimal]] = {}
    for row in rows:
        amount = _amount(row["amount_krw"])
        volume = _amount(row["volume_kg"])
        if amount is None or volume is None:
            # 잴 수 없는 행. ``HAVING sum(trade_volume_kg) > 0`` 과 같은 처분이다 — 0으로
            # 채우면 "그 등급이 0원"이라는 없는 사실이 만들어지고, 죽으면 오케스트레이터가
            # 원인을 못 받는다 (규칙 3 · 결정 d). 등급이 전부 빠지면 0건 사유로 이어진다.
            continue
        carried = totals.setdefault(str(row["grade"]), [Decimal(0), Decimal(0)])
        totals[str(row["grade"])] = [carried[0] + amount, carried[1] + volume]
    quotes = []
    # 선언한 등급 순서대로 낸다. ⑥의 근거 문장이 ``market_quotes[0]`` 을 대표값으로 읽으므로
    # 순서가 흔들리면 같은 날 근거 문구가 달라진다.
    for grade in cfg["grades"]:
        summed = totals.get(grade)
        if summed is None or summed[1] <= 0:
            continue
        price = to_price(krw_per_kg(summed[0], summed[1]))
        if price <= 0:
            # ``grade_unit_price`` 는 스키마가 ``gt=0`` 이라, 0 이하가 한 줄이라도 섞이면
            # 제안 전체가 출력 경계에서 죽는다. 그 등급 하나를 빼는 쪽이 맞다.
            continue
        quotes.append(
            {
                "market": cfg["market_category"],
                "grade": grade,
                "price": price,
                "spec": label,
                # as_of 가 아니라 실제 관측일이다. 12-30 값을 12-31 시세라고 적으면
                #   그것도 거짓이다 — 사유·근거·ref_id 가 전부 이 값을 가져간다.
                "observed_at": observed,
                # 시장이 마지막으로 열린 날과, 그 뒤로 우리가 놓친 개장일 수. 노드는 DB 를
                # 모르므로 값이 여기서 실려 가야 순수 함수가 판정할 수 있다 — ``spec``·
                # ``observed_at`` 를 같은 이유로 얹는 것과 같다.
                "market_last_open": market_last_open,
                "trading_days_behind": behind,
            }
        )
    return quotes


def _single_observed_at(rows: list[dict[str, Any]]) -> str | None:
    """쿼리가 고른 하루. 두 날짜가 섞여 오면 멈춘다.

    쿼리가 ``max(auction_date)`` 하나로 좁히므로 정상 경로에서는 항상 한 날이다. 그런데도
    검사하는 이유: 등급별로 각자 거슬러 올라가는 형태로 쿼리가 바뀌면 스프레드가 서로 다른
    시점의 두 가격을 비교하게 되고, 그건 에러가 아니라 조용히 틀린 판단이 된다.
    """
    # 전 행에 있어야 한다. ``if row.get(...)`` 로 걸러 읽으면, 한 행만 날짜가 있고
    #   나머지는 없을 때 "단일 날짜"로 인정해 함께 합산한다 (Codex 2차 지적).
    missing = [row for row in rows if not row.get("observed_at")]
    if rows and missing:
        raise ValueError(
            f"관측일 없는 행이 {len(missing)}건 섞였다 — 어느 날 값인지 모르는 행을 "
            f"합산하면 물량가중이 서로 다른 시점을 섞는다"
        )
    dates = {str(row["observed_at"]) for row in rows}
    if not dates:
        return None
    if len(dates) > 1:
        raise ValueError(
            f"관측일이 하루가 아니다: {sorted(dates)} — 등급별로 다른 날을 집으면 "
            f"스프레드가 다른 시점의 두 가격을 비교하게 된다 (규칙 4)"
        )
    return dates.pop()
