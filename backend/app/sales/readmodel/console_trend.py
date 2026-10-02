"""판매 추이 read model — 날짜별로 접은 판매 사실.

판매 현황 화면이 «언제 얼마나 팔렸나» 를 묻는데, 다른 read model 은 그 축이 없다.
`console_partners` 는 거래처별로, `dashboard.load_item_summaries` 는 품목별로 접고,
`load_recent_sales` 는 최근 몇 건만 준다 — 날짜로 접은 계열은 여기뿐이다.

화면이 접지 않는다. 최근 판매 목록을 받아 프론트에서 날짜별로 더하면, 그 목록은
`limit` 이 걸린 일부라 합계가 조용히 틀린다. 접는 일은 여기서 끝낸다.

`sim_run_id` 에 기본값이 없다. 빠뜨리면 전 실행이 한 그래프에 섞인다.

SQL 은 `repository/console_trend.py`, 응답 모델과 `MAX_TREND_DAYS` 는
`schemas/console_trend.py` 다.
"""

from datetime import date

from app.core import db as core_db
from app.sales.repository.console_trend import load_daily_sales
from app.sales.schemas.console_trend import MAX_TREND_DAYS, SalesTrendPoint, SalesTrendResponse


def get_console_sales_trend(
    *,
    sim_run_id: str,
    as_of: date,
    days: int = MAX_TREND_DAYS,
    from_date: date | None = None,
    to_date: date | None = None,
) -> SalesTrendResponse:
    """이 실행의 날짜별 판매를 요청한 기간 안에서 읽는다."""
    if from_date is not None and to_date is not None and from_date > to_date:
        raise ValueError("from_date must not be after to_date")
    upper = as_of if to_date is None else min(to_date, as_of)
    with core_db.read_connection() as conn:
        rows = load_daily_sales(
            conn,
            sim_run_id=sim_run_id,
            upper=upper,
            from_date=from_date,
            limit=min(days, MAX_TREND_DAYS),
        )
    return SalesTrendResponse(
        sim_run_id=sim_run_id,
        as_of=as_of,
        rows=[SalesTrendPoint(**row) for row in rows],
    )
