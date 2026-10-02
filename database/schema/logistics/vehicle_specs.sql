-- vehicle_specs — 물류
--
-- 적용 순서는 `database/new_database_order.txt`.

BEGIN;

-- ═══════════════════════════════════════════════════════════════════════════
-- §9  운송 기준정보 — 차량 · 거리구간 운임 · 비용 근거
--
--     공유 DB 에 있던 운송 기준정보를 저장소로 회수한 표다. 고정 Route 정책 표
--        (route_code · direction · origin/destination_code · vehicle_class ·
--         max_load_kg · fixed_fee_krw · standard_minutes)는 스키마에 없다 — 고정 Route 는
--        `logistics_contracts` 의 거리 · 차급 · 건당 운송비를 읽는다
--        (`app/logistics/repository/transport.py`).
--        `vehicle_rate_table` 의 km 구간 12 행은 지우지도 바꾸지도 않는다 —
--        `deliveries` 15 행이 이 체계로 적혀 있다.
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS haetdeul.vehicle_specs (
    vehicle_class          TEXT NOT NULL,
    body_type              TEXT NOT NULL,
    max_payload_kg         NUMERIC(12,3) NOT NULL,
    operational_payload_kg NUMERIC(12,3) NOT NULL,
    inner_width_mm         INTEGER,
    inner_length_mm        INTEGER,
    inner_height_mm        INTEGER,
    max_pallet_floor_count INTEGER,
    source_ref             TEXT NOT NULL,
    evidence_grade         TEXT NOT NULL,
    note                   TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT vehicle_specs_pkey PRIMARY KEY (vehicle_class),
    CONSTRAINT ck_vehicle_specs_body_type
        CHECK (body_type IN ('REEFER', 'DRY', 'OPEN')),
    CONSTRAINT ck_vehicle_specs_payload
        CHECK (max_payload_kg > 0 AND operational_payload_kg > 0
               AND operational_payload_kg <= max_payload_kg),
    CONSTRAINT ck_vehicle_specs_grade
        CHECK (evidence_grade IN ('OFFICIAL', 'VENDOR', 'ASSUMED'))
);

COMMENT ON TABLE haetdeul.vehicle_specs IS
    '대표 차량 제원 (06 §3). 차량 선택은 중량 하나가 아니라 kg + Pallet + 높이를 함께 본다 (06 §2).';

COMMENT ON COLUMN haetdeul.vehicle_specs.operational_payload_kg IS
    '명목 최대적재량이 아니라 보수적인 내부 운영 Payload (06 §8).';

COMMIT;
