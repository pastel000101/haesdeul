-- zone_override_approvals — 물류
--
-- 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE IF NOT EXISTS haetdeul.zone_override_approvals (
    override_id      TEXT NOT NULL,
    pallet_id        TEXT,
    lot_id           TEXT,
    expected_zone_id TEXT,
    target_zone_id   TEXT NOT NULL,
    override_reason  TEXT NOT NULL,
    approved_by      TEXT NOT NULL,
    approved_at      TIMESTAMPTZ NOT NULL,
    note             TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT zone_override_approvals_pkey PRIMARY KEY (override_id),
    CONSTRAINT zone_override_approvals_pallet_id_fkey
        FOREIGN KEY (pallet_id) REFERENCES haetdeul.pallets(pallet_id),
    CONSTRAINT zone_override_approvals_lot_id_fkey
        FOREIGN KEY (lot_id) REFERENCES haetdeul.inventory_lots(lot_id),
    CONSTRAINT zone_override_approvals_expected_zone_id_fkey
        FOREIGN KEY (expected_zone_id) REFERENCES haetdeul.warehouse_zones(zone_id),
    CONSTRAINT zone_override_approvals_target_zone_id_fkey
        FOREIGN KEY (target_zone_id) REFERENCES haetdeul.warehouse_zones(zone_id),
    -- 무엇에 대한 예외인지 없는 승인은 두지 않는다.
    CONSTRAINT ck_zone_override_target
        CHECK (pallet_id IS NOT NULL OR lot_id IS NOT NULL)
);

COMMENT ON TABLE haetdeul.zone_override_approvals IS
    '기본 Zone 을 벗어난 배치의 승인 기록 (03 §7). 사유·승인자·승인시각 없이는 예외 배치를 두지 않는다.';

COMMIT;
