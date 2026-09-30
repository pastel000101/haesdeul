-- inventory_count_lines — 물류
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/30_logistics_wms_schema.sql` (2026-09-05 실 DB 에서 회수한 WMS 구조)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE IF NOT EXISTS haetdeul.inventory_count_lines (
    count_line_id     BIGSERIAL NOT NULL,
    count_session_id  TEXT NOT NULL,
    location_id       TEXT,
    pallet_id         TEXT,
    lot_id            TEXT,
    physical_presence BOOLEAN NOT NULL,
    physical_qty_kg   NUMERIC(18,6),
    system_qty_kg     NUMERIC(18,6),
    abnormal_flag     BOOLEAN NOT NULL DEFAULT FALSE,
    counted_by        TEXT NOT NULL,
    counted_at        TIMESTAMPTZ NOT NULL,
    note              TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT inventory_count_lines_pkey PRIMARY KEY (count_line_id),
    CONSTRAINT inventory_count_lines_count_session_id_fkey
        FOREIGN KEY (count_session_id) REFERENCES haetdeul.inventory_count_sessions(count_session_id),
    CONSTRAINT inventory_count_lines_location_id_fkey
        FOREIGN KEY (location_id) REFERENCES haetdeul.storage_locations(location_id),
    CONSTRAINT inventory_count_lines_pallet_id_fkey
        FOREIGN KEY (pallet_id) REFERENCES haetdeul.pallets(pallet_id),
    CONSTRAINT inventory_count_lines_lot_id_fkey
        FOREIGN KEY (lot_id) REFERENCES haetdeul.inventory_lots(lot_id),
    CONSTRAINT ck_inventory_count_lines_qty
        CHECK (physical_qty_kg IS NULL OR physical_qty_kg >= 0)
);

COMMENT ON TABLE haetdeul.inventory_count_lines IS
    '실사 입력 한 줄 (04 §3). system_qty_kg 는 Blind Count 라 입력 후에 채운다 — 미입력(NULL)과 0 을 섞지 않는다.';

COMMIT;
