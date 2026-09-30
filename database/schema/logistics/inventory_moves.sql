-- inventory_moves — 물류
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/10_domain_schema.sql` (2026-08-30 test DB 에서 뜬 pg_dump 스냅샷)
--   옛 `database/30_logistics_wms_schema.sql` (2026-09-05 실 DB 에서 회수한 WMS 구조)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.inventory_moves (
    move_id text NOT NULL,
    sim_run_id text NOT NULL,
    lot_id text NOT NULL,
    sale_item_id text,
    move_type text NOT NULL,
    quantity_kg numeric(18,6) NOT NULL,
    moved_at date NOT NULL,
    reason_code text NOT NULL,
    note text,
    CONSTRAINT inventory_moves_move_type_check CHECK ((move_type = ANY (ARRAY['IN'::text, 'OUT'::text, 'DISPOSE'::text, 'ADJUST'::text]))),
    CONSTRAINT inventory_moves_quantity_kg_check CHECK ((quantity_kg > (0)::numeric))
);

COMMENT ON TABLE haetdeul.inventory_moves IS 'Lot의 입고·출고·폐기·조정 이동 원장.';

COMMENT ON COLUMN haetdeul.inventory_moves.move_id IS '재고 이동 ID.';

COMMENT ON COLUMN haetdeul.inventory_moves.sim_run_id IS '시뮬레이션 실행 ID.';

COMMENT ON COLUMN haetdeul.inventory_moves.lot_id IS '재고 Lot ID.';

COMMENT ON COLUMN haetdeul.inventory_moves.sale_item_id IS '판매 Detail ID.';

COMMENT ON COLUMN haetdeul.inventory_moves.move_type IS '재고 이동 유형.';

COMMENT ON COLUMN haetdeul.inventory_moves.quantity_kg IS '이동 수량. 양수로 저장하고 방향은 move_type으로 구분한다.';

COMMENT ON COLUMN haetdeul.inventory_moves.moved_at IS '재고 이동일.';

COMMENT ON COLUMN haetdeul.inventory_moves.reason_code IS '재고 이동 사유 코드.';

COMMENT ON COLUMN haetdeul.inventory_moves.note IS '추가 설명 및 주의사항.';

ALTER TABLE ONLY haetdeul.inventory_moves
    ADD CONSTRAINT inventory_moves_pkey PRIMARY KEY (move_id);

ALTER TABLE ONLY haetdeul.inventory_moves
    ADD CONSTRAINT fk_inventory_moves_sim_run FOREIGN KEY (sim_run_id) REFERENCES haetdeul.sim_runs(sim_run_id);

ALTER TABLE ONLY haetdeul.inventory_moves
    ADD CONSTRAINT inventory_moves_lot_id_fkey FOREIGN KEY (lot_id) REFERENCES haetdeul.inventory_lots(lot_id);

ALTER TABLE ONLY haetdeul.inventory_moves
    ADD CONSTRAINT inventory_moves_sale_item_id_fkey FOREIGN KEY (sale_item_id) REFERENCES haetdeul.sale_items(sale_item_id);

-- `inventory_move_lines` 의 복합 FK 가 이 UNIQUE 를 필요로 한다.
-- Move 한 건의 Line 이 **다른 Lot** 을 가리키지 못하게 하는 장치다.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'uq_inventory_moves_id_lot'
                     AND conrelid = 'haetdeul.inventory_moves'::regclass) THEN
        ALTER TABLE haetdeul.inventory_moves
            ADD CONSTRAINT uq_inventory_moves_id_lot UNIQUE (move_id, lot_id);
    END IF;
END
$$;

COMMIT;
