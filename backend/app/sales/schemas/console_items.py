"""판매 운영 화면이 고르는 활성 품목 — `readmodel/console_items.py` 가 채운다."""

from pydantic import BaseModel


class ConsoleItem(BaseModel):
    item_id: str
    item_code: str
    item_name: str
    base_unit: str


class ConsoleItemsResponse(BaseModel):
    rows: list[ConsoleItem]
