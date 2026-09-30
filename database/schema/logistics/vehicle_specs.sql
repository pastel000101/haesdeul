-- vehicle_specs — 물류
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/30_logistics_wms_schema.sql` (2026-09-05 실 DB 에서 회수한 WMS 구조)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

-- ═══════════════════════════════════════════════════════════════════════════
-- §9  운송 기준정보 — 차량 · 거리구간 운임 · 비용 근거
--
--     🔴 **여기 있는 것은 회수일 뿐이다.** 고정 Route 정책
--        (route_code · direction · origin/destination_code · vehicle_class ·
--         max_load_kg · fixed_fee_krw · standard_minutes)은 **후속 단계**다.
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
