-- pallet_events — 물류
--
-- 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE IF NOT EXISTS haetdeul.pallet_events (
    pallet_event_id  BIGSERIAL NOT NULL,
    pallet_id        TEXT NOT NULL,
    event_type       TEXT NOT NULL,
    from_location_id TEXT,
    to_location_id   TEXT,
    occurred_at      TIMESTAMPTZ NOT NULL,
    recorded_by      TEXT NOT NULL,
    note             TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pallet_events_pkey PRIMARY KEY (pallet_event_id),
    CONSTRAINT pallet_events_pallet_id_fkey
        FOREIGN KEY (pallet_id) REFERENCES haetdeul.pallets(pallet_id),
    CONSTRAINT pallet_events_from_location_id_fkey
        FOREIGN KEY (from_location_id) REFERENCES haetdeul.storage_locations(location_id),
    CONSTRAINT pallet_events_to_location_id_fkey
        FOREIGN KEY (to_location_id) REFERENCES haetdeul.storage_locations(location_id),
    CONSTRAINT ck_pallet_events_type
        CHECK (event_type IN ('CREATED', 'PUTAWAY', 'RELOCATED', 'HOLD_MOVED', 'EMPTIED'))
);

CREATE INDEX IF NOT EXISTS idx_pallet_events_pallet
    ON haetdeul.pallet_events (pallet_id, occurred_at DESC);

COMMENT ON TABLE haetdeul.pallet_events IS
    '수량변동 없는 Pallet 위치이동 이력 (02 §7). 수량이 바뀌는 것은 inventory_moves 쪽이다.';

COMMIT;
