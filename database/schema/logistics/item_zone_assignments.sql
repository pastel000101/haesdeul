-- item_zone_assignments — 물류
--
-- 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE IF NOT EXISTS haetdeul.item_zone_assignments (
    item_id    TEXT NOT NULL,
    zone_id    TEXT NOT NULL,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    allowed    BOOLEAN NOT NULL DEFAULT TRUE,
    source_ref TEXT NOT NULL,
    note       TEXT,
    CONSTRAINT item_zone_assignments_pkey PRIMARY KEY (item_id, zone_id),
    CONSTRAINT item_zone_assignments_item_id_fkey
        FOREIGN KEY (item_id) REFERENCES haetdeul.items(item_id),
    CONSTRAINT item_zone_assignments_zone_id_fkey
        FOREIGN KEY (zone_id) REFERENCES haetdeul.warehouse_zones(zone_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_item_zone_assignments_default
    ON haetdeul.item_zone_assignments (item_id) WHERE is_default;

COMMENT ON TABLE haetdeul.item_zone_assignments IS
    '품목별 허용 보관 Zone (03 §7). 기본 Zone 을 벗어나려면 zone_override_approvals 가 있어야 한다.';

COMMIT;
