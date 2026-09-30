-- inventory_move_lines — 물류
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/30_logistics_wms_schema.sql` (2026-09-05 실 DB 에서 회수한 WMS 구조)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

-- ═══════════════════════════════════════════════════════════════════════════
-- §7  원장 상세 — Move Line
--     ★ `inventory_moves` 헤더는 `10_domain_schema.sql` 소유다. 여기는 상세다.
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS haetdeul.inventory_move_lines (
    move_line_id BIGSERIAL NOT NULL,
    move_id      TEXT NOT NULL,
    lot_id       TEXT NOT NULL,
    pallet_id    TEXT,
    location_id  TEXT,
    quantity_kg  NUMERIC(18,6) NOT NULL,
    note         TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT inventory_move_lines_pkey PRIMARY KEY (move_line_id),
    -- 🔴 복합 FK 둘이 "Line 의 Lot" 을 헤더·Pallet 과 **강제로 일치**시킨다.
    --    단일 FK 셋으로 나누면 Line 이 다른 Lot 을 가리켜도 DB 가 안 막는다.
    CONSTRAINT fk_move_lines_move_lot
        FOREIGN KEY (move_id, lot_id) REFERENCES haetdeul.inventory_moves(move_id, lot_id),
    CONSTRAINT fk_move_lines_pallet_lot
        FOREIGN KEY (pallet_id, lot_id) REFERENCES haetdeul.pallets(pallet_id, lot_id),
    CONSTRAINT inventory_move_lines_lot_id_fkey
        FOREIGN KEY (lot_id) REFERENCES haetdeul.inventory_lots(lot_id),
    CONSTRAINT inventory_move_lines_location_id_fkey
        FOREIGN KEY (location_id) REFERENCES haetdeul.storage_locations(location_id),
    CONSTRAINT ck_inventory_move_lines_qty CHECK (quantity_kg > 0)
);

CREATE INDEX IF NOT EXISTS idx_inventory_move_lines_move
    ON haetdeul.inventory_move_lines (move_id);

CREATE INDEX IF NOT EXISTS idx_inventory_move_lines_pallet
    ON haetdeul.inventory_move_lines (pallet_id);

COMMENT ON TABLE haetdeul.inventory_move_lines IS
    '수량 원장의 Pallet 단위 내역 (02 §3·§10). Lot 수량과 Pallet 수량을 같은 원장에서 계산한다 (02 §14).';

COMMENT ON COLUMN haetdeul.inventory_move_lines.lot_id IS
    'Move 헤더·Pallet 과 **같은 Lot 이어야 한다** — 복합 FK 두 개가 강제한다.';

COMMENT ON COLUMN haetdeul.inventory_move_lines.pallet_id IS
    'Pallet 확정 전 입고 등에서는 NULL 이다. 없는 Pallet 을 지어내지 않는다.';

COMMIT;
