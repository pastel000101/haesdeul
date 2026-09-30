-- item_packaging_specs — 물류
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/30_logistics_wms_schema.sql` (2026-09-05 실 DB 에서 회수한 WMS 구조)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

-- ═══════════════════════════════════════════════════════════════════════════
-- §2  품목 기준정보 — 포장 · 회전 · Zone 배정
--     🔴 `items` 를 FK 로 가리키기만 한다. `items` 자체는 건드리지 않는다.
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS haetdeul.item_packaging_specs (
    packaging_spec_id           TEXT NOT NULL,
    item_id                     TEXT NOT NULL,
    package_type                TEXT NOT NULL,
    nominal_unit_weight_kg      NUMERIC(10,3) NOT NULL,
    length_mm                   INTEGER,
    width_mm                    INTEGER,
    height_mm                   INTEGER,
    default_units_per_pallet    INTEGER,
    default_kg_per_pallet       NUMERIC(12,3) NOT NULL,
    max_loaded_pallet_height_mm INTEGER,
    source_ref                  TEXT NOT NULL,
    evidence_grade              TEXT NOT NULL,
    is_default                  BOOLEAN NOT NULL DEFAULT FALSE,
    note                        TEXT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT item_packaging_specs_pkey PRIMARY KEY (packaging_spec_id),
    CONSTRAINT item_packaging_specs_item_id_fkey
        FOREIGN KEY (item_id) REFERENCES haetdeul.items(item_id),
    CONSTRAINT ck_item_packaging_specs_type
        CHECK (package_type IN ('NET', 'BOX', 'BULK')),
    CONSTRAINT ck_item_packaging_specs_weight CHECK (nominal_unit_weight_kg > 0),
    CONSTRAINT ck_item_packaging_specs_kg_plt CHECK (default_kg_per_pallet > 0),
    CONSTRAINT ck_item_packaging_specs_grade
        CHECK (evidence_grade IN ('OFFICIAL', 'VENDOR', 'SIM_FIXED', 'ASSUMED'))
);

-- 품목당 기본 규격은 하나다. 부분 UNIQUE 라 제약이 아니라 인덱스로 선다.
CREATE UNIQUE INDEX IF NOT EXISTS uq_item_packaging_specs_default
    ON haetdeul.item_packaging_specs (item_id) WHERE is_default;

COMMENT ON TABLE haetdeul.item_packaging_specs IS
    '품목별 포장·Pallet 환산 기준정보 (02 §8). kg → Pallet Position 환산의 정본이다.';

COMMENT ON COLUMN haetdeul.item_packaging_specs.length_mm IS
    '그물망은 고정형상이 아니므로 NULL 허용 (02 §8). 없는 치수를 지어내지 않는다.';

COMMENT ON COLUMN haetdeul.item_packaging_specs.default_kg_per_pallet IS
    '보수적 Simulation 값이다. 실제 Pallet 적재시험 결과가 아니다 (01 §8).';

COMMIT;
