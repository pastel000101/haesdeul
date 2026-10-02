-- warehouses — 물류
--
-- 적용 순서는 `database/new_database_order.txt`.

BEGIN;

-- ═══════════════════════════════════════════════════════════════════════════
-- §1  창고 물리 골격 — Warehouse → Zone → Location
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS haetdeul.warehouses (
    warehouse_id                            TEXT NOT NULL,
    warehouse_name                          TEXT NOT NULL,
    network_type                            TEXT NOT NULL,
    operation_model                         TEXT NOT NULL,
    contract_type                           TEXT NOT NULL,
    region                                  TEXT,
    area_pyeong                             NUMERIC(10,2),
    area_m2                                 NUMERIC(12,2),
    clear_height_m                          NUMERIC(6,2),
    top_airflow_clearance_m                 NUMERIC(6,2),
    usable_storage_envelope_m               NUMERIC(6,2),
    pallet_standard                         TEXT,
    pallet_length_mm                        INTEGER,
    pallet_width_mm                         INTEGER,
    storage_equipment                       TEXT,
    storage_levels                          INTEGER,
    operational_max_loaded_pallet_height_mm INTEGER,
    handling_equipment                      TEXT,
    handling_equipment_capacity_ton         NUMERIC(6,2),
    working_aisle_mm                        INTEGER,
    geometry_basis                          TEXT NOT NULL,
    source_ref                              TEXT NOT NULL,
    note                                    TEXT,
    created_at                              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT warehouses_pkey PRIMARY KEY (warehouse_id),
    CONSTRAINT ck_warehouses_network_type
        CHECK (network_type IN ('SINGLE_HUB_MVP', 'MULTI_HUB')),
    CONSTRAINT ck_warehouses_operation_model
        CHECK (operation_model IN ('LEASED_SELF_OPERATED', 'OUTSOURCED_3PL')),
    CONSTRAINT ck_warehouses_contract_type
        CHECK (contract_type IN ('LEASE', 'OWNED')),
    CONSTRAINT ck_warehouses_geometry_basis
        CHECK (geometry_basis IN ('SIMULATION_GEOMETRY', 'MEASURED'))
);

COMMENT ON TABLE haetdeul.warehouses IS
    '창고 물리 기준정보. 치수·높이는 실측이 아니라 Simulation Geometry다 (Persona 01 §1).';

COMMENT ON COLUMN haetdeul.warehouses.operational_max_loaded_pallet_height_mm IS
    '적재 Pallet 운영 상한(mm). 기하학적 최대 1650이 아니라 보수적 운영한계 1500이다 (01 §4).';

COMMENT ON COLUMN haetdeul.warehouses.geometry_basis IS
    'SIMULATION_GEOMETRY = 비교매물·면적에서 만든 가정 형상. 실측 전에는 이 값을 바꾸지 않는다.';

COMMIT;
