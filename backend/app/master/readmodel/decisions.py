"""결정 이력 조회 — 조회 연결을 빌려 `master_decisions` 를 읽고 현재 결정을 표시한다.

호출 하나에 조회 연결(autocommit) 하나를 빌린다. SQL 은 `repository/decisions.py`.
라우터 · 이력 조회 · ask 는 결정 목록을 이 함수로 읽는다.
"""

from __future__ import annotations

from app.core import db as core_db
from app.core.settings import get_db_schema
from app.master.domain.decision import mark_current
from app.master.repository.decisions import row_to_out, select_decisions
from app.master.schemas.decision import DecisionOut


def list_decisions(request_id: str) -> list[DecisionOut]:
    """한 요청에 붙은 결정 전부. 오래된 것부터.

    최신 하나만 `is_current=True` 로 표시된다 — 이력은 지우지 않고 접는다.
    """
    schema = get_db_schema()
    with core_db.read_connection() as conn:
        rows = select_decisions(conn, request_id, schema=schema)
    return mark_current([row_to_out(dict(row)) for row in rows])
