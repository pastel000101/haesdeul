"""개장 정본 조회 — 연결을 빌려 `master_day_openings` 를 읽는다(실패는 없음으로 접는다).

★ 2026-09-30 재구성 BL-018: `master/day_opening_repository.py` 에서 옮겼다 — `read_day_opening`,
  `opened_days_after`.
  연결 종류(`connection()` 또는 넘겨받은 `borrow`) · 실패를 `None` 으로 접는 자리 · 문장을 연결보다
  먼저
  짓는 순서는 종전 그대로다. SQL 은 `repository/day_openings.py`. 로그 이름은 이
  모듈(`app.master.readmodel.day_openings`)이다.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from contextlib import ExitStack
from datetime import date

from app.core import db as core_db
from app.core.settings import get_db_schema
from app.master.repository.day_openings import (
    COLUMNS,
    select_day_opening,
    select_opened_days_after,
)
from app.master.schemas.day_open import DayOpeningRecord

logger = logging.getLogger(__name__)


def read_day_opening(
    *, as_of: date, sim_run_id: str, borrow: core_db.Borrow | None = None
) -> DayOpeningRecord | None:
    """그 날의 개장 정본. **없으면 `None` 이고 그것은 *"한 번도 안 불렀다"* 다.**

    ⚠️ **못 읽은 것도 `None` 이다.** 관문이 이 값을 못 읽었다고 판단을 멈추면 안 되고,
      그때는 근사를 쓰되 **근사라는 것을 사유에 적는다** (`day_gate`).
    """
    schema = get_db_schema()  # ★ 종전처럼 문장(스키마 이름)을 연결보다 먼저 짓는다

    open_connection = core_db.connection if borrow is None else borrow
    with ExitStack() as stack:
        try:
            conn = stack.enter_context(open_connection())
        except Exception:
            logger.exception("개장 정본 커넥션 실패 - 근사로 답한다")
            return None
        try:
            row = select_day_opening(conn, as_of=as_of, sim_run_id=sim_run_id, schema=schema)
        except Exception:
            logger.exception("개장 정본 조회 실패 - 근사로 답한다")
            return None
    if row is None:
        return None
    # ★ `dict_row` 면 Mapping, 아니면 순서 튜플이다. 조회 컬럼 순서와 짝이다.
    if isinstance(row, Mapping):
        values = [row[name] for name in COLUMNS]
    else:
        values = list(row)
    as_of_v, sim_v, result_v, attempt_v, failure_v, reason_v = values
    return DayOpeningRecord(
        as_of=as_of_v,
        sim_run_id=sim_v,
        result=result_v,
        attempt_count=int(attempt_v),
        failure_count=int(failure_v),
        reason=reason_v,
    )


def opened_days_after(
    *, after: date, sim_run_id: str, borrow: core_db.Borrow | None = None
) -> tuple[date, ...] | None:
    """`after` **보다 뒤에** 이미 열린 날들. 오래된 것부터.

    🔴 **왜 이 함수가 필요한가** (물류 물음 2026-09-07 · 실측 2026-09-07).

      승인 전이는 `target_state_date`(= 승인일 + 1) **한 행에만** 쓴다. 그런데 그
      다음 날들이 **이미 열려 있으면** 그 행들은 승인 이전의 전날에서 물려받은
      것이라 **새 도착분을 모른다.**

      ```text
      2026-01-14   in_transit 2건   ← 승인이 여기 들어갔다
      2026-01-15   in_transit 1건   ← **도착일인데 새 것이 없다**
      ```

      ★ 정방향 운영에서는 안 생긴다 — 내일은 아직 없으니까. 다만 *"내일을 미리 열어
        두고 오늘 승인"* 은 실제로 있을 수 있는 순서이고, 실측 장부가 그 상태였다.

    🔴 **마스터가 물류 표를 읽지 않는다** (정의서 §3.2.5). 어느 날이 열렸는지는
       `master_day_openings` 가 아는 **마스터 사실**이라 여기서 답할 수 있다.

       ⚠️ 그래서 **정본에 없는 날은 안 보인다.** 이 표가 생기기 전에 열린 날은
         마스터도 모르고, 그것은 근사가 아니라 **모르는 것**이다 — 지어내지 않는다.

    ★ **성공한 개장만 센다.** `NOT_OPENED` · `REJECTED_GAP` 인 날은 파트 행이 안 섰
      으므로 물려줄 것도 없다.

    🔴 **못 읽으면 `None` 이다. 빈 튜플이 아니다** (물류 지적 2026-09-07).

      ```text
      ()      앞질러 열린 날이 **없다**        정방향이다
      None    **못 읽었다**                    있었는지조차 모른다
      ```

      ⚠️ 둘을 `()` 하나로 접으면 낡은 미래 행이 남아 있는데도 호출자가 *"따라잡을
        것이 없었다"* 로 읽는다. **없는 것과 못 읽은 것은 다르다.**

    ★ 못 읽는 것이 승인을 멈추지는 않는다 — `record_day_opening` 이 절대 raise 하지
      않는 것과 같은 규율이다. 다만 그 사실이 `TransitionOut.carried_forward_status`
      로 나간다.
    """
    schema = get_db_schema()  # ★ 종전처럼 문장(스키마 이름)을 연결보다 먼저 짓는다

    open_connection = core_db.connection if borrow is None else borrow
    with ExitStack() as stack:
        try:
            conn = stack.enter_context(open_connection())
        except Exception:
            logger.exception("개장 정본 조회 실패 - 앞질러 열린 날을 모른 채 간다")
            return None
        try:
            rows = select_opened_days_after(conn, after=after, sim_run_id=sim_run_id, schema=schema)
        except Exception:
            logger.exception("개장 정본 조회 실패 - 앞질러 열린 날을 모른 채 간다")
            return None
    return tuple(row["as_of"] if isinstance(row, Mapping) else row[0] for row in rows)
