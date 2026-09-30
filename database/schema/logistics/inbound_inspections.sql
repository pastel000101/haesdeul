-- inbound_inspections — 물류
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/30_logistics_wms_schema.sql` (2026-09-05 실 DB 에서 회수한 WMS 구조)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE IF NOT EXISTS haetdeul.inbound_inspections (
    inspection_id    TEXT NOT NULL,
    receipt_id       TEXT NOT NULL,
    inspected_at     TIMESTAMPTZ NOT NULL,
    inspector        TEXT NOT NULL,
    verdict          TEXT NOT NULL,
    inspected_qty_kg NUMERIC(18,6) NOT NULL,
    accepted_qty_kg  NUMERIC(18,6) NOT NULL DEFAULT 0,
    hold_qty_kg      NUMERIC(18,6) NOT NULL DEFAULT 0,
    reject_qty_kg    NUMERIC(18,6) NOT NULL DEFAULT 0,
    fact_source      TEXT NOT NULL,
    note             TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT inbound_inspections_pkey PRIMARY KEY (inspection_id),
    CONSTRAINT inbound_inspections_receipt_id_fkey
        FOREIGN KEY (receipt_id) REFERENCES haetdeul.inbound_receipts(receipt_id),
    CONSTRAINT ck_inbound_inspections_verdict
        CHECK (verdict IN ('PASS', 'HOLD', 'REJECT')),
    CONSTRAINT ck_inbound_inspections_fact_source
        CHECK (fact_source IN ('HUMAN_RECORDED', 'SCENARIO_SIMULATED')),
    -- 항등식이다. 셋의 합이 검수량과 다르면 어느 쪽이 맞는지 아무도 모른다.
    CONSTRAINT ck_inbound_inspections_qty
        CHECK (inspected_qty_kg > 0
               AND accepted_qty_kg >= 0 AND hold_qty_kg >= 0 AND reject_qty_kg >= 0
               AND accepted_qty_kg + hold_qty_kg + reject_qty_kg = inspected_qty_kg),
    -- 판정과 수량이 서로를 배반하지 못하게 한다.
    CONSTRAINT ck_inbound_inspections_verdict_qty
        CHECK ((verdict = 'PASS' AND hold_qty_kg = 0 AND reject_qty_kg = 0)
               OR (verdict = 'HOLD' AND hold_qty_kg > 0)
               OR (verdict = 'REJECT' AND accepted_qty_kg = 0 AND reject_qty_kg > 0))
);

COMMENT ON TABLE haetdeul.inbound_inspections IS
    'MVP 공통 품질검수 결과 (03 §4). 품목별 전문 판정은 고도화 대상이다.';

COMMENT ON COLUMN haetdeul.inbound_inspections.inspected_qty_kg IS
    'accepted + hold + reject 와 정확히 같아야 한다. 도착량과 검수량의 차이는 inbound_receipts 쪽 축이다.';

COMMIT;
