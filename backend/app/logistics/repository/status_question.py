"""질문형 조회 SQL — 품목명 → `item_id`."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from psycopg import sql

from app.logistics.repository.rows import get_db_schema


def select_item_ids_by_name(conn: Any, unique: Sequence[str]) -> dict[str, str]:
    """품목명 → `item_id` (`items` 표가 정본). 없는 이름은 빠진다."""
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "SELECT item_id, item_name FROM {schema}.items WHERE item_name = ANY(%(names)s)"
            ).format(schema=schema),
            {"names": list(unique)},
        )
        by_name = {row["item_name"]: row["item_id"] for row in cursor.fetchall()}
    return by_name
