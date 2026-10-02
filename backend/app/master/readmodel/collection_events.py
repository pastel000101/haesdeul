"""수금 사건 조회 — 연결을 빌려 `master_collection_events` 를 읽는다.

넘겨받은 `borrow` 가 없으면 `core_db.connection()`(autocommit 아님 · commit 하지 않고 반환)을
빌린다. 마스터 수금 단계의 연결과는 다른 연결이다(재무 파트가 사건을 먼저 읽는다).
SQL 은 `repository/collection_events.py`.
"""

from __future__ import annotations

from app.core import db as core_db
from app.core.settings import get_db_schema
from app.finance.schemas.collections import CollectionEvent
from app.master.repository.collection_events import select_collection_events


def read_collection_events(
    *,
    sim_run_id: str,
    financing_mode: str,
    borrow: core_db.Borrow | None = None,
) -> tuple[CollectionEvent, ...]:
    """`(sim_run_id, financing_mode)` 축의 수금 사건 전부. 오래된 날짜부터.

    축 둘로 거른다. 실측으로 `finance_states` 에 `LOAN_BASELINE` 252행과 `BASE_NO_LOAN`
    2행이 공존한다. `financing_mode` 를 안 걸면 무차입 장부의 수금이 대출 baseline
    장부에 조용히 섞이고, 그 사고는 에러 없이 숫자만 바꾼다.

    날짜로는 안 거른다. "오늘 것" 을 고르는 일은 재무
    (`DeterministicCollectionFixtureSource.events_for_date`)가 한다 — 축의 사건을
    통째로 넘기고 그쪽이 그날 것을 고른다.

    실패 처리: 못 읽으면 예외를 그대로 올린다. `()` 로 접으면 "오늘 들어올 게
    없었다" 와 구별할 수 없다. 접는 판단은 부르는 쪽 몫이다.

    :returns: 그 축의 사건들. 빈 튜플은 "사건이 없다" 이고 그것은 정상이다.
    """
    schema = get_db_schema()  # 문장을 짓고(스키마 이름) 나서 연결을 빌린다
    open_connection = core_db.connection if borrow is None else borrow
    with open_connection() as conn:
        return select_collection_events(
            conn, sim_run_id=sim_run_id, financing_mode=financing_mode, schema=schema
        )
