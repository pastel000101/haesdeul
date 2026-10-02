"""에이전트 네 mode 가 같이 쓰는 스냅샷 읽기 — 부재는 `None`, 실행 오류는 `SnapshotLoadError`.

연결은 `readmodel/current.read_current_logistics` 가 읽기마다 따로 빌린다(그 모듈 머리의 표).
"""

from __future__ import annotations

import logging
from datetime import date

from app.logistics.readmodel.current import read_current_logistics
from app.logistics.schemas.current import LogisticsRead

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 도우미
# ---------------------------------------------------------------------------


class SnapshotLoadError(Exception):
    """스냅샷 조회가 실행 오류로 실패했다 — 데이터 부재(LookupError)와 다르다."""


def load_read(*, as_of: date, sim_run_id: str) -> LogisticsRead | None:
    """부재만 None 으로, 실행 오류는 구분해 올린다 (#121 4단계).

    `sim_run_id` 는 봉투에서 온다. 여기서 지어내지 않는다 (#345). "어느 실행의 장부인가"
    는 물류 사실이 아니라 마스터가 소유한 값이다(`contracts/envelope.ExecutionContext`).
    그래서 `BURN_IN_SIM_RUN_ID` 로 메우지도, 최신 실행을 고르지도, DB 에서 되짚지도
    않는다 — 그 전부가 fail-open 이다.

    선택 인자로 두지 않는다. 어댑터 경로에는 값이 없는 경우가 없다 — `logistics_port` 의
    문이 이미 막는다(`no_run_axis_reply`). 여기에 `None` 을 남기면 "안 주면 조용히
    넓어지는" 자리가 다시 생긴다. Repository 쪽 `sim_run_id=None` 은 독립 Service 경로
    (`service/cycle._get_snapshot_or_none`) 때문에 남는 것이지 어댑터 때문이 아니다.

    Repository 예외 계약 — 정상적인 "데이터 없음/미확정"은 LookupError 둘이다(runtime
    fixture 0건 · 필수 정책 미등재). 독립 Service 경로(`service/cycle._get_snapshot_or_none`)
    가 같은 기준선이다.

    그 외 — ValueError/TypeError(데이터는 있는데 모양이 깨졌거나 활성 fixture 가 중복인
    무결성 위반), RuntimeError(env 부재), psycopg 오류(DB 장애) — 는 회사 상태가 아니라
    실행 실패다. RUNTIME_NOT_READY 로 뭉개면 마스터가 재시도하지 않고 "데이터를 달라"로
    오독한다 (M-1 §5.1 — ERROR 가 재시도 가치가 있는 쪽).

    `item_storage_policies.operational_limit_days` 는 DB 에서 nullable 이고, 그 NULL 은
    "보관한계 미등록"(부재)이다 (#366). 어댑터 특례로 TypeError 를 걸러내지 않고 Repository
    어휘에서 부재로 읽는다 — 부재는 `remaining_freshness_days=None` 으로 나와
    `evaluate_sales_rules` 의 `N17-LOT`(`N17_LOT_FRESHNESS_UNRESOLVED`)이 받는다. 여기서
    TypeError 만 걸러내면 "어떤 TypeError 는 상태" 라는 예외의 예외가 생기고, 같은 NULL 을
    읽는 독립 Service 경로는 그대로 실패한다. 없는 사실은 UNRESOLVED 로, 실행 실패만
    ERROR 로 가른다.
    """
    try:
        return read_current_logistics(as_of=as_of, sim_run_id=sim_run_id)
    except LookupError:
        return None  # 없는 것은 예외가 아니라 상태다
    except Exception as error:
        # 원문 메시지는 로그로만 남긴다 — reasoning 에 그대로 실으면 숫자가 섞여
        # E-REASONING-NUMERIC 에 걸린다 (재무 400 회신에서 확인된 함정).
        logger.exception("Logistics read failed (as_of=%s, sim_run_id=%s)", as_of, sim_run_id)
        raise SnapshotLoadError from error
