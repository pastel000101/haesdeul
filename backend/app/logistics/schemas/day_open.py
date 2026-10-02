"""하루 넘김의 실패 종류. 순서는 `service/day_open.py` 에 있다."""

from __future__ import annotations


class LogisticsRunAmbiguous(ValueError):
    """같은 `(as_of, usage_scope)` 에 서로 다른 실행의 활성 행이 둘 이상인데,
    이 하루 넘김이 어느 실행인지 듣지 못했다.

    여기서 하나를 고르지 않는다. 첫 행도 최신도 고르는 것이고, 고르면 그
    순간 다른 실행의 어제가 이 실행의 오늘이 된다 —
    `readmodel/current.get_active_logistics_runtime_fixture` 가 활성 fixture 2건에서
    하나를 고르지 않는 것과 같은 규율이다.

    `LogisticsFixtureMissing`(부재)과 다른 사실이다. 저쪽은 "물려받을 곳이
    없다" 이고 여기는 "물려받을 곳이 여럿이라 못 고른다" 다. 부재로 접으면
    무결성 위반이 "데이터를 주세요" 로 나간다.

    이 예외가 뜨는 것은 `sim_run_id` 를 안 받은 배선뿐이다. 받았으면 조회가 그
    실행으로 좁혀져 있어 애초에 둘이 보이지 않는다. 마스터 등록소 조립
    (`master/registry/bootstrap.py`)은 `LogisticsDayOpening(sim_run_id=...)` 로
    주입한다.
    """
