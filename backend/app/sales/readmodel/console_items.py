"""판매 운영 화면이 사용하는 활성 품목 정본 조회.

★ 2026-09-29 BL-013: `sales/console_items.py` 에서 옮겼다. SQL 은 `repository/console_items.py`.
"""

from app.core import db as core_db
from app.sales.repository.console_items import load_active_items
from app.sales.schemas.console_items import ConsoleItem, ConsoleItemsResponse


def get_console_items() -> ConsoleItemsResponse:
    """공용 ``items`` 원장에서 판매 화면이 선택할 활성 품목을 읽는다."""
    with core_db.read_connection() as conn:
        rows = load_active_items(conn)
    return ConsoleItemsResponse(rows=[ConsoleItem.model_validate(row) for row in rows])
