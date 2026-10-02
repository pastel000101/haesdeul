-- inventory_allocations — 물류
--
-- 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE IF NOT EXISTS haetdeul.inventory_allocations (
    allocation_id    TEXT NOT NULL,
    reservation_id   TEXT NOT NULL,
    lot_id           TEXT NOT NULL,
    pallet_id        TEXT,
    allocated_qty_kg NUMERIC(18,6) NOT NULL,
    allocation_basis TEXT NOT NULL,
    decided_by       TEXT NOT NULL,
    decided_at       TIMESTAMPTZ NOT NULL,
    status           TEXT NOT NULL,
    note             TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT inventory_allocations_pkey PRIMARY KEY (allocation_id),
    CONSTRAINT inventory_allocations_reservation_id_fkey
        FOREIGN KEY (reservation_id) REFERENCES haetdeul.inventory_reservations(reservation_id),
    CONSTRAINT inventory_allocations_lot_id_fkey
        FOREIGN KEY (lot_id) REFERENCES haetdeul.inventory_lots(lot_id),
    CONSTRAINT inventory_allocations_pallet_id_fkey
        FOREIGN KEY (pallet_id) REFERENCES haetdeul.pallets(pallet_id),
    -- FEFO_AUTO_SELECTED 는 시뮬레이션 자동 FEFO 할당이다. FEFO_TOOL_CONFIRMED ·
    --    HUMAN_OVERRIDE 는 사람이 무엇을 했나를 적는 값이라 사람이 없는 선택을 담을 수 없어
    --    따로 둔다. 이미 쓰는 DB 의 어휘 확장은
    --    `migrations/logistics/logistics_allocation_basis_fefo_auto.sql` 이다.
    CONSTRAINT ck_inventory_allocations_basis
        CHECK (allocation_basis IN ('FEFO_TOOL_CONFIRMED', 'HUMAN_OVERRIDE',
                                    'FEFO_AUTO_SELECTED')),
    CONSTRAINT ck_inventory_allocations_status
        CHECK (status IN ('ALLOCATED', 'PICKED', 'SHIPPED', 'CANCELLED')),
    CONSTRAINT ck_inventory_allocations_qty CHECK (allocated_qty_kg > 0)
);

CREATE INDEX IF NOT EXISTS idx_inventory_allocations_reservation
    ON haetdeul.inventory_allocations (reservation_id);

CREATE INDEX IF NOT EXISTS idx_inventory_allocations_lot
    ON haetdeul.inventory_allocations (lot_id);

COMMENT ON TABLE haetdeul.inventory_allocations IS
    '출고 준비 시 실제 Lot/Pallet 지정 (02 §11). 한 주문이 여러 Lot/Pallet 에서 충당될 수 있다.';

COMMENT ON COLUMN haetdeul.inventory_allocations.allocation_basis IS
    'FEFO_TOOL_CONFIRMED = Tool 후보를 사람이 그대로 확정 / HUMAN_OVERRIDE = 사람이 다르게 정함 / FEFO_AUTO_SELECTED = 사람 없이 FEFO 규칙이 고름 (시뮬레이션). 기본값을 두지 않는다 — 호출자가 반드시 말한다.';

COMMIT;
