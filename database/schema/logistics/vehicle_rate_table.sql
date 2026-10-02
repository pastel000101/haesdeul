-- vehicle_rate_table — 물류
--
-- 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE IF NOT EXISTS haetdeul.vehicle_rate_table (
    rate_id          TEXT NOT NULL,
    vehicle_class    TEXT NOT NULL,
    body_type        TEXT NOT NULL,
    distance_from_km NUMERIC(10,3) NOT NULL,
    distance_to_km   NUMERIC(10,3) NOT NULL,
    base_rate_krw    NUMERIC(18,2) NOT NULL,
    rate_type        TEXT NOT NULL,
    evidence_grade   TEXT NOT NULL,
    source_ref       TEXT NOT NULL,
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    note             TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT vehicle_rate_table_pkey PRIMARY KEY (rate_id),
    CONSTRAINT uq_vehicle_rate_band
        UNIQUE (vehicle_class, body_type, distance_from_km, distance_to_km, rate_type),
    CONSTRAINT vehicle_rate_table_vehicle_class_fkey
        FOREIGN KEY (vehicle_class) REFERENCES haetdeul.vehicle_specs(vehicle_class),
    CONSTRAINT ck_vehicle_rate_distance
        CHECK (distance_from_km >= 0 AND distance_to_km > distance_from_km),
    CONSTRAINT ck_vehicle_rate_amount CHECK (base_rate_krw > 0),
    CONSTRAINT ck_vehicle_rate_type
        CHECK (rate_type IN ('PUBLIC_REFERENCE', 'SIMULATION_BASELINE',
                             'VENDOR_QUOTE', 'SETTLEMENT_RATE')),
    CONSTRAINT ck_vehicle_rate_grade
        CHECK (evidence_grade IN ('OFFICIAL', 'VENDOR', 'ASSUMED'))
);

COMMENT ON TABLE haetdeul.vehicle_rate_table IS
    '거리구간 운임표 (06 §5). 단일 won_per_km 를 정책 정본으로 쓰지 않는다.';

COMMENT ON COLUMN haetdeul.vehicle_rate_table.distance_to_km IS
    '구간은 distance_from_km 초과 ~ distance_to_km 이하다. 예: (0,11] = 문서의 "~11km".';

COMMIT;
