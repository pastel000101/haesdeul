"""ML 예측을 계약 형태(`Forecast`)로 읽는다 — 서비스 창고 `ml_price_forecasts`.

`purchase_agent.ports.get_forecast` 가 이걸 쓰면 된다 (옛 `app/ml/service.py` 머리말).

★ **앱 안에서 부르는 곳이 없다** (2026-09-29 확인). 이 함수를 쓰던 `GET /ml/forecast` 는
  2026-09-11 에 주석 처리됐다(`app/api/ml/qa.py`). 지울지는 재구성 쟁점 4(죽은 코드 범위)라
  여기서는 자리만 옮겼다.

🟢 **자리 (2026-09-29 · 재구성 BL-017).** 전에는 `app/ml/service.py` 에 적재와 함께 있었다.
  조회 결과를 계약 모양으로 조립하는 일이라 readmodel 로 뗐다 — 조회 연결 하나를 빌려
  `repository/forecasts.py` 에 넘긴다. 적재는 `service/forecasts.py` 다.
"""

from __future__ import annotations

from datetime import date

from app.contracts.forecast import DailyPoint, Forecast, TargetKind
from app.core import db as core_db
from app.ml.repository.forecasts import latest_forecast_rows
from app.ml.schemas.forecast import HORIZON_DAYS


def get_forecast(item: str, as_of: date, target_kind: TargetKind = "AUC") -> Forecast:
    """계약 형태로 예측을 돌려준다.

    ``as_of`` **이하** 의 가장 최근 기준일을 쓴다. 그날 예측이 없으면
    전날 것을 준다 — 미래 정보를 쓰지 않으면서 값이 비지 않게 한다.
    """
    with core_db.read_connection() as conn:
        rows = latest_forecast_rows(conn, item=item, target_kind=target_kind, as_of=as_of)
    if not rows:
        raise LookupError(f"{item}·{target_kind}·{as_of} 이전 예측이 없습니다.")

    head = rows[0]
    return Forecast(
        as_of=head["base_dt"],
        item=head["item_nm"],
        target_kind=head["target_kind"],
        unit=head["unit"],
        current_price=head["current_price"],
        horizon_days=HORIZON_DAYS,
        model_version=head["model_version"],
        generated_at=head["generated_at"],
        daily=[
            DailyPoint(date=r["target_dt"], predicted=r["predicted"],
                       lower=r["lower"], upper=r["upper"])
            for r in rows
        ],
        market_name=head["market_name"],
        grade_name=head["grade_name"],
        spec_desc=head["spec_desc"],
        use_recommended=head["use_recommended"],
        quality_note=head["quality_note"],
        filled_count=sum(1 for r in rows if r["is_filled"]),
    )
