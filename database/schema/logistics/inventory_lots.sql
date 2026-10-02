-- inventory_lots — 물류
--
-- 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.inventory_lots (
    lot_id text NOT NULL,
    sim_run_id text NOT NULL,
    purchase_item_id text NOT NULL,
    item_id text NOT NULL,
    -- grade · derivation_status 는 NOT NULL 이 아니다.
    --    실제로 도착·검수를 거친 Lot 은 권위 있는 등급이 없을 수 있고
    --    (`purchase_items.grade` 가 NULL 이며 `상품 → 상` 매핑은 금지다),
    --    Burn-in 파생도 아니다. 없는 것을 NULL 로 적는다 — 대체값을 지어내지 않는다.
    --    이미 쓰는 DB 용 이관판은 `database/migrations/logistics/logistics_inventory_lots_nullable.sql`.
    grade text,
    received_at date NOT NULL,
    original_qty_kg numeric(18,6) NOT NULL,
    remaining_qty_kg numeric(18,6) NOT NULL,
    unit_cost_krw_per_kg numeric(18,6) NOT NULL,
    storage_zone text NOT NULL,
    status text NOT NULL,
    derivation_status text,
    CONSTRAINT inventory_lots_check CHECK ((remaining_qty_kg <= original_qty_kg)),
    CONSTRAINT inventory_lots_original_qty_kg_check CHECK ((original_qty_kg >= (0)::numeric)),
    CONSTRAINT inventory_lots_remaining_qty_kg_check CHECK ((remaining_qty_kg >= (0)::numeric)),
    CONSTRAINT inventory_lots_status_check CHECK ((status = ANY (ARRAY['ACTIVE'::text, 'DEPLETED'::text, 'DISPOSED'::text, 'HOLD'::text])))
);

COMMENT ON TABLE haetdeul.inventory_lots IS '입고로 생성된 재고 Lot의 현재 상태. 잔여신선도는 저장하지 않고 as_of 기준으로 계산한다.';

COMMENT ON COLUMN haetdeul.inventory_lots.lot_id IS '재고 Lot ID.';

COMMENT ON COLUMN haetdeul.inventory_lots.sim_run_id IS '시뮬레이션 실행 ID.';

COMMENT ON COLUMN haetdeul.inventory_lots.purchase_item_id IS '매입 Detail ID.';

COMMENT ON COLUMN haetdeul.inventory_lots.item_id IS '품목 고유 ID.';

COMMENT ON COLUMN haetdeul.inventory_lots.grade IS '권위 있는 품질등급. 미확정이면 NULL.';

COMMENT ON COLUMN haetdeul.inventory_lots.received_at IS '입고일.';

COMMENT ON COLUMN haetdeul.inventory_lots.original_qty_kg IS 'Lot 최초 입고수량(kg).';

COMMENT ON COLUMN haetdeul.inventory_lots.remaining_qty_kg IS '현재 Lot 잔량. inventory_moves의 IN-OUT-DISPOSE와 정합해야 한다.';

COMMENT ON COLUMN haetdeul.inventory_lots.unit_cost_krw_per_kg IS 'Lot 원가단가(원/kg).';

COMMENT ON COLUMN haetdeul.inventory_lots.storage_zone IS '보관 Zone 코드.';

COMMENT ON COLUMN haetdeul.inventory_lots.status IS '상태값.';

COMMENT ON COLUMN haetdeul.inventory_lots.derivation_status IS 'Burn-in Lot이 어떤 파생규칙으로 생성됐는지 나타내는 상태. Burn-in이 아닌 Lot은 NULL.';

ALTER TABLE ONLY haetdeul.inventory_lots
    ADD CONSTRAINT inventory_lots_pkey PRIMARY KEY (lot_id);

ALTER TABLE ONLY haetdeul.inventory_lots
    ADD CONSTRAINT fk_inventory_lots_sim_run FOREIGN KEY (sim_run_id) REFERENCES haetdeul.sim_runs(sim_run_id);

ALTER TABLE ONLY haetdeul.inventory_lots
    ADD CONSTRAINT inventory_lots_item_id_fkey FOREIGN KEY (item_id) REFERENCES haetdeul.items(item_id);

ALTER TABLE ONLY haetdeul.inventory_lots
    ADD CONSTRAINT inventory_lots_purchase_item_id_fkey FOREIGN KEY (purchase_item_id) REFERENCES haetdeul.purchase_items(purchase_item_id);

-- ═══════════════════════════════════════════════════════════════════════════
-- §4  공유 DB 에서 자란 물류 칸 · 제약 — `inventory_lots` · `inventory_moves`
--
--     위 `CREATE TABLE` 은 공유 DB 의 pg_dump 스냅샷 문장이다. 그 뒤 실 DB 에서 자란
--        칸·제약은 스냅샷에 없어서 여기서 멱등 ALTER 로 더한다.
--
--     순서가 여기인 이유: 아래 FK 가 §2·§3 의 표(`item_packaging_specs` ·
--        `inbound_receipts`)를 가리킨다. 적용 목록에서 그 파일들이 먼저 온다.
-- ═══════════════════════════════════════════════════════════════════════════
ALTER TABLE haetdeul.inventory_lots
    ADD COLUMN IF NOT EXISTS packaging_spec_id  TEXT,
    ADD COLUMN IF NOT EXISTS harvest_date       DATE,
    ADD COLUMN IF NOT EXISTS inbound_receipt_id TEXT,
    ADD COLUMN IF NOT EXISTS source_partner_id  TEXT,
    ADD COLUMN IF NOT EXISTS inspection_status  TEXT,
    ADD COLUMN IF NOT EXISTS lot_note           TEXT;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'fk_inventory_lots_packaging_spec'
                     AND conrelid = 'haetdeul.inventory_lots'::regclass) THEN
        ALTER TABLE haetdeul.inventory_lots
            ADD CONSTRAINT fk_inventory_lots_packaging_spec
            FOREIGN KEY (packaging_spec_id)
            REFERENCES haetdeul.item_packaging_specs(packaging_spec_id);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'fk_inventory_lots_inbound_receipt'
                     AND conrelid = 'haetdeul.inventory_lots'::regclass) THEN
        ALTER TABLE haetdeul.inventory_lots
            ADD CONSTRAINT fk_inventory_lots_inbound_receipt
            FOREIGN KEY (inbound_receipt_id)
            REFERENCES haetdeul.inbound_receipts(receipt_id);
    END IF;

    -- ⚠️ 다른 도메인(`partners`)을 **가리키기만** 한다. 그쪽 표는 안 바뀐다.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'fk_inventory_lots_source_partner'
                     AND conrelid = 'haetdeul.inventory_lots'::regclass) THEN
        ALTER TABLE haetdeul.inventory_lots
            ADD CONSTRAINT fk_inventory_lots_source_partner
            FOREIGN KEY (source_partner_id)
            REFERENCES haetdeul.partners(partner_id);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'ck_inventory_lots_inspection_status'
                     AND conrelid = 'haetdeul.inventory_lots'::regclass) THEN
        ALTER TABLE haetdeul.inventory_lots
            ADD CONSTRAINT ck_inventory_lots_inspection_status
            CHECK (inspection_status IS NULL
                   OR inspection_status IN ('PASS', 'HOLD', 'REJECT'));
    END IF;

    -- 검수에서 걸린 물량이 ACTIVE 로 앉아 가용재고에 섞이지 못하게 막는다.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'ck_inventory_lots_hold_not_available'
                     AND conrelid = 'haetdeul.inventory_lots'::regclass) THEN
        ALTER TABLE haetdeul.inventory_lots
            ADD CONSTRAINT ck_inventory_lots_hold_not_available
            CHECK (inspection_status IS NULL
                   OR inspection_status = 'PASS'
                   OR status <> 'ACTIVE');
    END IF;
END
$$;

COMMENT ON COLUMN haetdeul.inventory_lots.harvest_date IS
    '공급자가 준 수확일. 없으면 NULL 이다 — 추정해 만들지 않는다 (05 §3).';

COMMENT ON COLUMN haetdeul.inventory_lots.inspection_status IS
    '입고검수 판정 (PASS/HOLD/REJECT). 검수결과가 다른 물량은 같은 Lot 에 섞지 않는다 (03 §5).';

-- 이 COMMENT 문구는 공유 DB 의 문구를 그대로 회수한 것이다. 코드는 현재 잔량을 이 칸에서
--    읽고(`app/logistics/repository/current.py`), 과거 시점 잔량은 `inventory_moves` 원장으로
--    되살린다(`app/logistics/readmodel/historical.py`).
--    주의: 문구가 가리키는 `23_inventory_move_type_split.sql` 은 저장소에 없고,
--    `inventory_moves.move_type` 어휘는 `ADJUST` 하나다 — `ADJUST_IN` · `ADJUST_OUT` 은 없다.
COMMENT ON COLUMN haetdeul.inventory_lots.remaining_qty_kg IS
    'DERIVED_CACHE — inventory_moves 집계의 현재 잔량이다. 정본은 원장이다: IN - OUT - DISPOSE + ADJUST_IN - ADJUST_OUT (02 §3·§14). ADJUST_IN/ADJUST_OUT 어휘는 23_inventory_move_type_split.sql 적용 후에 쓸 수 있다. 직접 UPDATE 로 뜻을 만들지 않는다.';

COMMIT;
