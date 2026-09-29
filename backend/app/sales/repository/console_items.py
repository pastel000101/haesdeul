"""판매 운영 화면이 고르는 활성 품목 SQL — 공용 `items` 원장.

★ 2026-09-29 BL-013: `sales/console_items.py` 에서 SQL 을 옮겼다.
"""

from typing import Any

from psycopg import Connection, sql

from app.core.settings import get_db_schema
from app.sales.repository._cursor import fetch_all


def load_active_items(conn: Connection) -> list[dict[str, Any]]:
    """공용 ``items`` 원장에서 판매 화면이 선택할 활성 품목 행."""
    return fetch_all(
        conn,
        sql.SQL("""
            SELECT item_id, item_code, item_name, base_unit
            FROM {}.items
            WHERE mvp_active
            ORDER BY item_name, item_id
        """).format(sql.Identifier(get_db_schema())),
    )
