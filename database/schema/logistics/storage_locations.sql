-- storage_locations — 물류
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/30_logistics_wms_schema.sql` (2026-09-05 실 DB 에서 회수한 WMS 구조)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

-- ★ `UNIQUE NULLS NOT DISTINCT` 는 PostgreSQL 15+ 다. 실 DB 는 17.10 이다.
--   FLOOR_POSITION 은 rack/bay/level 이 NULL 이라, NULL 을 서로 다른 값으로 보는
--   기본 규칙이면 같은 자리를 여러 번 등록해도 안 막힌다.
CREATE TABLE IF NOT EXISTS haetdeul.storage_locations (
    location_id   TEXT NOT NULL,
    warehouse_id  TEXT NOT NULL,
    zone_id       TEXT NOT NULL,
    lane_code     TEXT,
    rack_code     TEXT,
    bay_code      TEXT,
    level_no      INTEGER,
    position_no   INTEGER NOT NULL,
    location_kind TEXT NOT NULL,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    note          TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT storage_locations_pkey PRIMARY KEY (location_id),
    CONSTRAINT uq_storage_locations_address UNIQUE NULLS NOT DISTINCT
        (warehouse_id, zone_id, lane_code, rack_code, bay_code, level_no, position_no),
    CONSTRAINT storage_locations_warehouse_id_fkey
        FOREIGN KEY (warehouse_id) REFERENCES haetdeul.warehouses(warehouse_id),
    CONSTRAINT storage_locations_zone_id_fkey
        FOREIGN KEY (zone_id) REFERENCES haetdeul.warehouse_zones(zone_id),
    CONSTRAINT ck_storage_locations_kind
        CHECK (location_kind IN ('RACK_POSITION', 'FLOOR_POSITION')),
    CONSTRAINT ck_storage_locations_position_no CHECK (position_no > 0),
    CONSTRAINT ck_storage_locations_rack_addressed
        CHECK (location_kind <> 'RACK_POSITION'
               OR (rack_code IS NOT NULL AND bay_code IS NOT NULL AND level_no IS NOT NULL)),
    CONSTRAINT ck_storage_locations_floor_addressed
        CHECK (location_kind <> 'FLOOR_POSITION'
               OR (rack_code IS NULL AND bay_code IS NULL AND level_no IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_storage_locations_zone
    ON haetdeul.storage_locations (zone_id, is_active);

COMMENT ON TABLE haetdeul.storage_locations IS
    'Warehouse→Zone→Lane→Rack→Bay→Level→Position 물리 위치 (02 §6). 한 행 = Pallet 한 자리다 — Zone 물리정본은 kg 가 아니라 Pallet Position 이다 (07 §6).';

COMMENT ON COLUMN haetdeul.storage_locations.location_kind IS
    'RACK_POSITION = Selective Pallet Rack 한 자리 / FLOOR_POSITION = 검수·HOLD·출고대기 Floor 한 자리. 둘 다 Pallet 한 장이다.';

COMMIT;
