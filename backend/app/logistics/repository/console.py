"""화면 조회 SQL — 품목 이름 · 발표 품목 ID.

★ 2026-09-30 재구성 BL-015: `logistics/console_service.py` 에서 옮겼다.
"""

from __future__ import annotations

from typing import Any

from psycopg import sql

from app.logistics.repository.rows import dict_rows, schema_identifier

# ── 공통 조회 ───────────────────────────────────────────────────────────


def select_item_names(conn: Any) -> dict[str, str]:
    rows = dict_rows(
        conn,
        sql.SQL("SELECT item_id, item_name FROM {}.items").format(schema_identifier()),
        [],
    )
    return {row["item_id"]: row["item_name"] for row in rows}


def select_mvp_item_ids(conn: Any) -> list[str]:
    """화면이 기본으로 보여 줄 품목. 재고가 0kg 이어도 칸은 서야 한다."""
    rows = dict_rows(
        conn,
        sql.SQL("SELECT item_id FROM {}.items WHERE mvp_active ORDER BY item_id").format(
            schema_identifier()
        ),
        [],
    )
    return [row["item_id"] for row in rows]
