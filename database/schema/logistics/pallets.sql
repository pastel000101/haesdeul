-- pallets — 물류
--
-- 적용 순서는 `database/new_database_order.txt`.

BEGIN;

-- ═══════════════════════════════════════════════════════════════════════════
-- §5  Pallet — 물리 취급단위
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS haetdeul.pallets (
    pallet_id           TEXT NOT NULL,
    lot_id              TEXT NOT NULL,
    packaging_spec_id   TEXT,
    current_location_id TEXT,
    status              TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    emptied_at          TIMESTAMPTZ,
    note                TEXT,
    CONSTRAINT pallets_pkey PRIMARY KEY (pallet_id),
    -- 한 자리에 Pallet 은 하나다.
    CONSTRAINT uq_pallets_location UNIQUE (current_location_id),
    -- `inventory_move_lines` 의 복합 FK 대상 — Line 의 Pallet 과 Lot 이 갈리지 못한다.
    CONSTRAINT uq_pallets_id_lot UNIQUE (pallet_id, lot_id),
    CONSTRAINT pallets_lot_id_fkey
        FOREIGN KEY (lot_id) REFERENCES haetdeul.inventory_lots(lot_id),
    CONSTRAINT pallets_packaging_spec_id_fkey
        FOREIGN KEY (packaging_spec_id) REFERENCES haetdeul.item_packaging_specs(packaging_spec_id),
    CONSTRAINT pallets_current_location_id_fkey
        FOREIGN KEY (current_location_id) REFERENCES haetdeul.storage_locations(location_id),
    CONSTRAINT ck_pallets_status
        CHECK (status IN ('ACTIVE', 'HOLD', 'EMPTIED', 'DISPOSED')),
    -- 살아 있는 Pallet 은 자리가 있고, 비운 Pallet 은 자리를 차지하지 않는다.
    CONSTRAINT ck_pallets_location_matches_status
        CHECK ((current_location_id IS NOT NULL) = (status IN ('ACTIVE', 'HOLD'))),
    CONSTRAINT ck_pallets_emptied_at_required
        CHECK (status <> 'EMPTIED' OR emptied_at IS NOT NULL),
    CONSTRAINT ck_pallets_emptied_at_allowed
        CHECK (emptied_at IS NULL OR status IN ('EMPTIED', 'DISPOSED'))
);

CREATE INDEX IF NOT EXISTS idx_pallets_lot ON haetdeul.pallets (lot_id);

COMMENT ON TABLE haetdeul.pallets IS
    '물리 취급단위. 1 Lot : N Pallet 허용, 1 Pallet : 1 Lot (02 §5). Pallet 별 현재 수량은 저장하지 않고 Move Line 에서 계산한다.';

COMMIT;
