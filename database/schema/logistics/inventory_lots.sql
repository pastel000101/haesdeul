-- inventory_lots — 물류
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/10_domain_schema.sql` (2026-08-30 test DB 에서 뜬 pg_dump 스냅샷)
--   옛 `database/30_logistics_wms_schema.sql` (2026-09-05 실 DB 에서 회수한 WMS 구조)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.inventory_lots (
    lot_id text NOT NULL,
    sim_run_id text NOT NULL,
    purchase_item_id text NOT NULL,
    item_id text NOT NULL,
    -- 🔴 grade · derivation_status 는 NOT NULL 이 아니다 (2026-09-05 · 물류 변경).
    --    실제로 도착·검수를 거친 Lot 은 권위 있는 등급이 없을 수 있고
    --    (`purchase_items.grade` 가 NULL 이며 `상품 → 상` 매핑은 금지다),
    --    Burn-in 파생도 아니다. 없는 것을 NULL 로 적는다 — 대체값을 지어내지 않는다.
    --    같은 변경의 이관 판은 `database/logistics_inventory_lots_nullable.sql` 이다.
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
-- §4  기존 물류 표의 회수분 — `inventory_lots` · `inventory_moves`
--
--     🔴 **두 표는 물류 소유이고 `10_domain_schema.sql` 에 이미 있다.**
--        실 DB 에서 자란 칸·제약이 그 파일에 안 담겨 있어 여기서 더한다.
--        `10_domain_schema.sql` 은 손대지 않는다 — 그 파일은 pg_dump 스냅샷이고
--        여러 파트의 표가 한 덩어리로 들어 있다. 물류 변경을 거기 섞으면
--        "물류가 남의 파일을 고쳤다" 가 되고, 같은 변경이 두 곳으로 갈린다.
--
--     ★ 순서가 여기인 이유: 아래 FK 가 §2·§3 의 신규 표를 가리킨다.
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

-- 🔴 **이 주석은 실 DB 의 현재 문구를 그대로 옮긴 것이다** — 물류가 이번 단계에서
--    정본을 옮기기로 정한 것이 아니다. 코드(`app/logistics/repository.py`)는 아직
--    Lot 잔량을 읽고 `inventory_moves` 를 읽지 않는다. 문구와 코드가 갈려 있다는
--    사실 자체가 회수 대상이라 원문 그대로 둔다 (판단은 다음 단계 몫이다).
--    ⚠️ 문구가 가리키는 `23_inventory_move_type_split.sql` 은 저장소에 없다.
COMMENT ON COLUMN haetdeul.inventory_lots.remaining_qty_kg IS
    'DERIVED_CACHE — inventory_moves 집계의 현재 잔량이다. 정본은 원장이다: IN - OUT - DISPOSE + ADJUST_IN - ADJUST_OUT (02 §3·§14). ADJUST_IN/ADJUST_OUT 어휘는 23_inventory_move_type_split.sql 적용 후에 쓸 수 있다. 직접 UPDATE 로 뜻을 만들지 않는다.';

COMMIT;
