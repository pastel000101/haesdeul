-- warehouse_zones — 물류
--
-- 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE IF NOT EXISTS haetdeul.warehouse_zones (
    zone_id           TEXT NOT NULL,
    warehouse_id      TEXT NOT NULL,
    zone_code         TEXT NOT NULL,
    zone_name         TEXT NOT NULL,
    zone_kind         TEXT NOT NULL,
    purpose           TEXT NOT NULL,
    temp_min_c        NUMERIC(6,2),
    temp_max_c        NUMERIC(6,2),
    rh_min_pct        NUMERIC(6,2),
    rh_max_pct        NUMERIC(6,2),
    environment_basis TEXT NOT NULL,
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    source_ref        TEXT NOT NULL,
    note              TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT warehouse_zones_pkey PRIMARY KEY (zone_id),
    CONSTRAINT uq_warehouse_zones_code UNIQUE (warehouse_id, zone_code),
    CONSTRAINT warehouse_zones_warehouse_id_fkey
        FOREIGN KEY (warehouse_id) REFERENCES haetdeul.warehouses(warehouse_id),
    CONSTRAINT ck_warehouse_zones_kind
        CHECK (zone_kind IN ('STORAGE_RACK', 'WORK_FLOOR')),
    CONSTRAINT ck_warehouse_zones_purpose
        CHECK (purpose IN ('NORMAL_STORAGE', 'RECEIVING_INSPECTION',
                           'HOLD_QUARANTINE', 'OUTBOUND_STAGING')),
    CONSTRAINT ck_warehouse_zones_environment_basis
        CHECK (environment_basis IN ('SIMULATION_ASSUMPTION', 'MEASURED'))
);

COMMENT ON TABLE haetdeul.warehouse_zones IS
    '보관 Zone 과 작업 Floor Area. 온습도는 MVP 가정값이며 센서 실측이 아니다 (Persona 05 §5).';

COMMENT ON COLUMN haetdeul.warehouse_zones.zone_kind IS
    'STORAGE_RACK = 정상재고 Rack Zone / WORK_FLOOR = 검수·HOLD·출고대기 작업 Floor. Capacity 축이 다르다 (01 §7).';

COMMENT ON COLUMN haetdeul.warehouse_zones.environment_basis IS
    'SIMULATION_ASSUMPTION = 온습도 센서 없이 정상 유지된다고 가정한 값 (05 §5).';

COMMIT;
