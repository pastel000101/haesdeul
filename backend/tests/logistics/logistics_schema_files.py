"""물류 `db` 검사가 임시 스키마에 세우는 저장소 DDL (2026-09-30 BL-021).

옛 검사는 `10_domain_schema.sql` 에서 표 블록을 뜨고 `30_` · `40_` 파일을 통째로 돌렸다.
지금은 표마다 파일이 하나라, 새 DB 적용 목록(`database/new_database_order.txt`)의 순서대로
필요한 표 파일만 돌린다 — 손으로 다시 적지 않는다(검사용 표와 실제 표가 갈리지 않게).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ORDER_FILE = REPO / "database" / "new_database_order.txt"

#: 옛 `30_logistics_wms_schema.sql` 이 만들던 WMS 표 22 · 뷰 2.
WMS = (
    "warehouses",
    "warehouse_zones",
    "storage_locations",
    "item_packaging_specs",
    "item_turnover_policies",
    "item_zone_assignments",
    "inbound_schedules",
    "inbound_receipts",
    "inbound_inspections",
    "inbound_inspection_checks",
    "pallets",
    "pallet_events",
    "zone_override_approvals",
    "inventory_reservations",
    "inventory_allocations",
    "inventory_move_lines",
    "inventory_count_sessions",
    "inventory_count_lines",
    "inventory_count_discrepancies",
    "vehicle_specs",
    "vehicle_rate_table",
    "logistics_cost_references",
    "v_zone_position_occupancy",
    "v_move_line_integrity",
)

#: 옛 `40_logistics_agent_schema.sql` 의 Agent 표 셋.
AGENT = ("logistics_exceptions", "logistics_investigations", "logistics_action_proposals")

_TRANSACTION_LINE = re.compile(r"(?m)^\s*(BEGIN|COMMIT)\s*;\s*$")


def _ordered_schema_files() -> list[Path]:
    paths = [
        REPO / line.strip()
        for line in ORDER_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return [path for path in paths if path.parent.parent.name == "schema"]


def _for_schema(path: Path, schema: str) -> str:
    text = _TRANSACTION_LINE.sub("", path.read_text(encoding="utf-8"))
    return text.replace("haetdeul.", f"{schema}.")


def schema_sql(schema: str, objects: Iterable[str]) -> str:
    """`objects`(표 · 뷰 이름 = 파일 이름) 의 스키마 파일을 적용 목록 순서대로 이어 붙인다."""
    wanted = set(objects)
    chosen = [path for path in _ordered_schema_files() if path.stem in wanted]
    missing = wanted - {path.stem for path in chosen}
    assert not missing, f"적용 목록에 없는 객체: {sorted(missing)}"
    return "\n".join(_for_schema(path, schema) for path in chosen)


def migration_sql(schema: str, relative: str) -> str:
    """`database/migrations/<relative>` 을 임시 스키마 이름으로 바꾼 본문."""
    return _for_schema(REPO / "database" / "migrations" / relative, schema)
