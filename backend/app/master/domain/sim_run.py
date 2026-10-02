"""실행(sim_run) 값 규칙 — 번인 축, 실행 ID 짓기, 요청 축 기본값, 실행 설정 칸 이름."""

from __future__ import annotations

from datetime import date

from app.master.schemas.procurement import ProcurementRunRequest
from app.master.schemas.sales import SalesRunRequest

#: 실행 이름의 머리. 읽는 사람을 위한 것이지 판정을 위한 것이 아니다.
SIM_RUN_ID_PREFIX = "SIM"


def build_sim_run_id(*, run_type: str, month: date, suffix: str | None = None) -> str:
    """실행 이름을 짓는다 — `SIM-{RUN_TYPE}-{YYYYMM}[-{구분}]`.

    짓기만 한다. 되읽지 않는다. 이 함수의 짝이 되는 파서를 만들지 않는다 — 실행 이름은
    읽는 사람을 위한 것이지 판정을 위한 것이 아니다 (`SIM_RUN_ID_PREFIX`).

    :param month: 어느 달의 실행인가. 날짜를 여기서 만들지 않는다 — 부르는 쪽이 이미
        기간을 알고 있고, 그 값에서 연월만 떼어 쓴다.
    :param suffix: 같은 달에 실행이 둘 이상일 때의 구분. 안 주면 안 붙인다.
    :raises ValueError: `run_type` 이 비었을 때. 빈 이름을 짓지 않는다.
    """
    if not run_type.strip():
        raise ValueError("run_type 없이 실행 이름을 지을 수 없다 — 종류의 주인은 그 칸이다")
    name = f"{SIM_RUN_ID_PREFIX}-{run_type.strip()}-{month.strftime('%Y%m')}"
    if suffix is None:
        return name
    if not suffix.strip():
        raise ValueError("빈 구분자를 붙이면 이름이 '-' 로 끝난다 — 안 줄 것이면 None 이다")
    return f"{name}-{suffix.strip()}"


#: 번인 구간의 시뮬레이션 키. 지금은 하나뿐이라 상수로 둔다 — 여러 개가 되면
#: 요청 파라미터로 올린다. 없는 값을 미리 만들지 않는다.
BURN_IN_SIM_RUN_ID = "SIM-BURNIN-202512"


def sim_run_id_of(request: ProcurementRunRequest | SalesRunRequest) -> str:
    """이번 실행의 축. 요청이 주면 그 값, 없으면 번인 상수.

    상수를 새로 만들지 않는다. 값의 주인은 이 모듈의 `BURN_IN_SIM_RUN_ID` 하나다 —
    문자열을 다른 곳에 다시 적으면 두 벌이 되고, 한쪽만 고치는 날 판단 행과 원장이
    갈린다.

    떨어졌다는 사실은 출처표가 적는다. 이 함수는 값만 고르고, "요청이 줬나 기본값인가" 는
    `service/procurement.py` 의 `_input_sources` 가 `REQUEST:` · `DEFAULT:` 로 나눠 적는다
    — 같은 사실을 두 자리에서 판정하지 않게 판정 규칙은 `_sim_run_source` 하나다.

    빈 문자열도 안 준 것으로 본다. 공백만 든 축을 그대로 봉투에 실으면
    `ledger.sim_run_id_for` 가 뒤에서 터지고, 그 실패는 "요청이 이상했다" 가 아니라
    "원장이 터졌다" 로 읽힌다.
    """
    given = (request.sim_run_id or "").strip()
    return given or BURN_IN_SIM_RUN_ID


PROVENANCE_CONFIG_KEY = "provenance"
"""`sim_runs.config_json` 안에서 어느 코드가 걸었는가가 앉는 칸 이름.

`BASELINE_CONFIG_KEY` 와 다른 칸이다. 그쪽은 출발점(어느 실행·어느 재무 상태)
이고 이쪽은 코드 자취다 — 축이 다른 둘을 한 칸에 뭉치지 않는다.

읽는 쪽이 문자열을 두 벌로 들지 않게 상수로 둔다 (`BACKFILL_CONFIG_KEY` 와 같은
이유). 실행을 여는 `service/sim_run.py` 가 이 칸을 적고, 걷기 시각을 덧붙이는
`repository/walk_provenance.py` 가 같은 이름으로 읽는다.
"""
