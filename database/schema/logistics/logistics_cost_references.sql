-- logistics_cost_references — 물류
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/30_logistics_wms_schema.sql` (2026-09-05 실 DB 에서 회수한 WMS 구조)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE IF NOT EXISTS haetdeul.logistics_cost_references (
    cost_ref_id    TEXT NOT NULL,
    cost_category  TEXT NOT NULL,
    cost_label     TEXT NOT NULL,
    amount_krw     NUMERIC(18,2),
    amount_basis   TEXT NOT NULL,
    value_status   TEXT NOT NULL,
    evidence_grade TEXT,
    source_ref     TEXT NOT NULL,
    excludes       TEXT,
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    note           TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT logistics_cost_references_pkey PRIMARY KEY (cost_ref_id),
    CONSTRAINT ck_logistics_cost_category
        CHECK (cost_category IN ('WAREHOUSE_BASE_COST', 'EQUIPMENT_CAPEX', 'RACK_CAPEX',
                                 'EQUIPMENT_DEPRECIATION', 'MAINTENANCE', 'ENERGY',
                                 'PALLET_COST', 'RACK_DEPRECIATION', 'RACK_INSPECTION_COST',
                                 'TRANSPORT')),
    CONSTRAINT ck_logistics_cost_basis
        CHECK (amount_basis IN ('ONE_TIME', 'MONTHLY', 'PER_DELIVERY')),
    CONSTRAINT ck_logistics_cost_grade
        CHECK (evidence_grade IS NULL
               OR evidence_grade IN ('OFFICIAL', 'VENDOR', 'ASSUMED')),
    -- 🔴 미확정을 금액으로 지어내지 못하게 한다. NOT_FIXED 면 금액도 등급도 NULL 이다.
    CONSTRAINT ck_logistics_cost_value_status
        CHECK ((value_status = 'FIXED' AND amount_krw IS NOT NULL AND evidence_grade IS NOT NULL)
               OR (value_status = 'NOT_FIXED' AND amount_krw IS NULL AND evidence_grade IS NULL))
);

COMMENT ON TABLE haetdeul.logistics_cost_references IS
    '물류 비용 근거 (01 §14·§16 · 07 §10). Finance 확정 CAPEX/OPEX 가 아니다 — 금액과 evidence_grade 를 항상 함께 전달한다.';

COMMENT ON COLUMN haetdeul.logistics_cost_references.evidence_grade IS
    'MVP 고정값으로 채택되어도 근거가 내부 파생이면 ASSUMED 를 유지한다 (2026-09-03 재무 요청 · 00 §9.1).';

COMMENT ON COLUMN haetdeul.logistics_cost_references.excludes IS
    '이 금액에 포함되지 않은 항목. Rack 재료비 1,550,000원을 총구축비로 전달하지 않기 위한 칸이다 (01 §14).';

COMMIT;
