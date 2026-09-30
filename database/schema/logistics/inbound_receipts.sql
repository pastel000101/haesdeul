-- inbound_receipts — 물류
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/30_logistics_wms_schema.sql` (2026-09-05 실 DB 에서 회수한 WMS 구조)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE IF NOT EXISTS haetdeul.inbound_receipts (
    receipt_id             TEXT NOT NULL,
    sim_run_id             TEXT NOT NULL,
    inbound_id             TEXT,
    purchase_item_id       TEXT,
    item_id                TEXT NOT NULL,
    arrived_at             DATE NOT NULL,
    receiving_location_id  TEXT,
    ordered_qty_kg         NUMERIC(18,6),
    accepted_qty_kg        NUMERIC(18,6),
    hold_qty_kg            NUMERIC(18,6),
    rejected_qty_kg        NUMERIC(18,6),
    estimated_pallet_count INTEGER,
    actual_pallet_count    INTEGER,
    receipt_status         TEXT NOT NULL,
    fact_source            TEXT NOT NULL,
    received_by            TEXT,
    note                   TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT inbound_receipts_pkey PRIMARY KEY (receipt_id),
    -- B-1 대조 키가 한 실행 안에서 두 번 서지 못하게 막는다.
    CONSTRAINT uq_inbound_receipts_inbound_id UNIQUE (sim_run_id, inbound_id),
    CONSTRAINT inbound_receipts_sim_run_id_fkey
        FOREIGN KEY (sim_run_id) REFERENCES haetdeul.sim_runs(sim_run_id),
    CONSTRAINT inbound_receipts_purchase_item_id_fkey
        FOREIGN KEY (purchase_item_id) REFERENCES haetdeul.purchase_items(purchase_item_id),
    CONSTRAINT inbound_receipts_item_id_fkey
        FOREIGN KEY (item_id) REFERENCES haetdeul.items(item_id),
    CONSTRAINT inbound_receipts_receiving_location_id_fkey
        FOREIGN KEY (receiving_location_id) REFERENCES haetdeul.storage_locations(location_id),
    CONSTRAINT ck_inbound_receipts_status
        CHECK (receipt_status IN ('ARRIVED', 'INSPECTING', 'INSPECTED', 'PUTAWAY_DONE', 'CLOSED')),
    CONSTRAINT ck_inbound_receipts_fact_source
        CHECK (fact_source IN ('HUMAN_RECORDED', 'SCENARIO_SIMULATED')),
    -- 미입력(NULL)은 0 으로 보지 않는다 — COALESCE 는 비교용이고 값을 만들지 않는다.
    CONSTRAINT ck_inbound_receipts_qty
        CHECK (COALESCE(ordered_qty_kg, 0) >= 0
               AND COALESCE(accepted_qty_kg, 0) >= 0
               AND COALESCE(hold_qty_kg, 0) >= 0
               AND COALESCE(rejected_qty_kg, 0) >= 0)
);

CREATE INDEX IF NOT EXISTS idx_inbound_receipts_arrival
    ON haetdeul.inbound_receipts (sim_run_id, arrived_at);

COMMENT ON TABLE haetdeul.inbound_receipts IS
    '입고 도착~검수~PUTAWAY 헤더 (03 §1). 재고 IN 수량은 주문수량이 아니라 accepted_qty_kg 다 (02 §2 · 03 §3).';

COMMENT ON COLUMN haetdeul.inbound_receipts.inbound_id IS
    'in_transit / confirmed_inbound_schedule 대조 키 (B-1). 같은 회차가 두 행이면 점유가 이중 계상된다.';

COMMENT ON COLUMN haetdeul.inbound_receipts.estimated_pallet_count IS
    '입고 전 kg/PLT 로 추정한 값. 확정은 검수 후 actual_pallet_count 다 (02 §9).';

COMMIT;
