"""재무 상태 조회 — 축 · as-of 상태 · 현재 상태 · Snapshot.

★ 공개 함수는 조회 연결을 **한 번** 빌려 SQL 을 같은 순서로 읽는다. `*_on(conn, …)` 은 이미
  빌린 조회 연결로 읽는 몸통이다 — 여러 조회를 한 연결로 묶는 쪽(런타임 컨텍스트 · DataPort)이 쓴다.

★ 2026-09-29 재구성 BL-014: `finance/db.py` 에서 옮겼다. 종전에는 SQL 마다 조회 연결을 빌렸다.
"""

from datetime import date
from typing import Any, cast

from app.core import db as core_db
from app.finance.domain.finance_state import (
    current_view_row,
    runtime_axis_from_rows,
    state_row_as_of,
)
from app.finance.repository.finance_states import (
    select_current_state_rows,
    select_runtime_axis,
    select_state_rows_as_of,
)
from app.finance.schemas.agent import FinanceSnapshot
from app.finance.schemas.finance_state import FinanceRuntimeAxis, FinanceState


def get_current_finance_state() -> FinanceState:
    """DB View가 지정한 현재 Finance State 한 건을 조회한다."""
    with core_db.read_connection() as conn:
        return cast(FinanceState, current_state_row_on(conn))


def get_current_finance_snapshot(
    as_of: date | None = None, *, sim_run_id: str | None = None
) -> FinanceSnapshot:
    """``as_of`` 시점의 Finance State를 T0 ID 미확정 Snapshot으로 변환한다."""
    with core_db.read_connection() as conn:
        return finance_snapshot_on(conn, as_of, sim_run_id=sim_run_id)


def finance_snapshot_on(
    conn: Any, as_of: date | None = None, *, sim_run_id: str | None = None
) -> FinanceSnapshot:
    """받은 조회 연결로 ``as_of`` 시점 상태를 Snapshot 으로 옮긴다."""
    return FinanceSnapshot(
        snapshot_id=None, **current_state_row_on(conn, as_of, sim_run_id=sim_run_id)
    )


def get_finance_runtime_axis(*, sim_run_id: str | None = None) -> FinanceRuntimeAxis:
    """**그 실행**이 서 있는 재무 축 — 시뮬레이션 실행과 조달 방식.

    ★ `v_current_finance_state` 에서 축을 읽는다. 그 View 는 이제 상태 ID 에 매여
      있지 않다 — `database/finance/finance_current_state_view.sql` 이 공유 기본
      스키마의 `finance_state_id = 'FIN-DAY30-LOAN'` 고정을 걷어내고, `sim_runs` 가
      정한 축에서 **가장 늦은 상태**를 돌려주도록 바꾼다.

    🔴 **`sim_run_id` 를 주면 그 실행만 본다.** 예전에는 View 전체에 대고
       *"시스템에 축이 하나뿐인가"* 를 물었다. 실행이 하나일 때는 같은 답이지만,
       번인과 새 걷기가 **공존하는 순간** 그 질문은 늘 *"둘"* 이라고 답한다 —
       실측으로 `SIM-BURNIN-202512` 와 `SIM-WALK-202601-LOAN` 이 함께 서자 새 걷기의
       첫 개장이 `finance_runtime_axis_ambiguous` 로 막혔다. **남의 실행이 있다는
       사실만으로 내 실행이 모호해지면 안 된다.**

    🔴 `financing_mode` 를 축에서 빼면 안 된다. 같은 sim_run · 같은 날짜에
       `BASE_NO_LOAN` 과 `LOAN_BASELINE` 두 행이 실제로 있다 — 날짜만으로 고르면
       **무차입 상태가 대출 baseline 자리에 조용히 들어온다.**

    🔴 **축이 여러 개면 고르지 않는다.** 좁혀 물었는데도 둘이면 그것은 *"같은 실행
       안에서 축이 갈렸다"* 이고, 거기서 아무거나 집으면 **남의 축 위에서 판단**하게
       된다. 그 사고는 에러 없이 숫자만 바꾼다.

    ⚠️ `sim_run_id` 를 안 주는 경로는 *"지금 상태"* 를 묻는 레거시 조회(STATUS 화면)
      뿐이다. 그때도 실행이 여럿이면 **고르지 않고 세운다** — 판단 경로는 전부
      축을 명시한다.

    ★ 현재 시점 조회는 여기까지다. 과거 시점 선택은 아래 as-of 질의가 한다 —
      View 는 "지금", 질의는 "그때" 를 맡는다.
    """
    with core_db.read_connection() as conn:
        return runtime_axis_on(conn, sim_run_id=sim_run_id)


def runtime_axis_on(conn: Any, *, sim_run_id: str | None = None) -> FinanceRuntimeAxis:
    """받은 조회 연결로 재무 축을 읽는다 (`get_finance_runtime_axis` 의 몸통)."""
    return runtime_axis_from_rows(select_runtime_axis(conn, sim_run_id=sim_run_id))


def load_finance_state_row(
    as_of: date, *, sim_run_id: str | None = None
) -> dict[str, object]:
    """``as_of`` 시점에 유효한 재무 상태 한 건. **미래를 읽지 않는다.**

    ```text
    같은 sim_run · 같은 financing_mode 안에서
    state_date <= as_of 중 가장 늦은 행
    ```

    🔴 최신 행 두 건이 **같은 날짜**면 고르지 않고 세운다. 승인 전이가 같은 날에
       상태를 하나 더 만들면 "가장 늦은 행" 이 둘이 되는데, 그중 하나를 말없이
       집으면 어느 쪽이 답인지 아무도 모른 채 숫자가 달라진다.

    ★ **`sim_run_id` 를 주면 그 실행의 축만 본다.** 판단·승인·전이 경로는 전부
      명시한다 — 실행이 여럿인 환경에서 축을 안 주면 *"지금 축이 하나뿐인가"* 라는
      다른 질문이 되고, 그 질문은 남의 실행 때문에 실패한다.
    """
    with core_db.read_connection() as conn:
        return finance_state_row_on(conn, as_of, sim_run_id=sim_run_id)


def finance_state_row_on(
    conn: Any, as_of: date, *, sim_run_id: str | None = None
) -> dict[str, object]:
    """받은 조회 연결로 ``as_of`` 시점 상태 한 건을 고른다."""
    axis = runtime_axis_on(conn, sim_run_id=sim_run_id)
    return state_row_as_of(
        select_state_rows_as_of(
            conn,
            sim_run_id=axis["sim_run_id"],
            financing_mode=axis["financing_mode"],
            as_of=as_of,
        )
    )


def current_state_row_on(
    conn: Any, as_of: date | None = None, *, sim_run_id: str | None = None
) -> dict[str, object]:
    """``as_of`` 를 주면 그 시점의 행, 주지 않으면 View 가 고정한 현재 행.

    ★ `as_of` 없는 경로는 "지금 상태" 를 묻는 조회(레거시 · STATUS 화면)다.
      판단 경로는 모두 `as_of` 를 넘긴다.

    🔴 **여러 실행 중 하나를 조용히 고르지 않는다.** 예전에는 `fetch_one` 이라
       View 가 실행마다 한 행씩 돌려줄 때 **아무 행이나** 집혔다. 번인과 새 걷기가
       공존하면 그 선택은 매번 달라질 수 있고, 그때 나오는 것은 오류가 아니라
       **남의 실행 잔액**이다. 터지는 편이 낫다.
    """
    if as_of is not None:
        return finance_state_row_on(conn, as_of, sim_run_id=sim_run_id)
    return current_view_row(select_current_state_rows(conn, sim_run_id=sim_run_id))
