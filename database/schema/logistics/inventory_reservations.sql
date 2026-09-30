-- inventory_reservations — 물류
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/30_logistics_wms_schema.sql` (2026-09-05 실 DB 에서 회수한 WMS 구조)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

-- ═══════════════════════════════════════════════════════════════════════════
-- §6  출고 준비 — Reservation · Allocation
--     🔴 `sales` 를 FK 로 가리키기만 한다. 판매 표는 안 바뀐다.
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS haetdeul.inventory_reservations (
    reservation_id  TEXT NOT NULL,
    sim_run_id      TEXT NOT NULL,
    item_id         TEXT NOT NULL,
    sale_id         TEXT,
    required_qty_kg NUMERIC(18,6) NOT NULL,
    reserved_qty_kg NUMERIC(18,6) NOT NULL DEFAULT 0,
    due_date        DATE,
    status          TEXT NOT NULL,
    -- 놓아준 시뮬레이션 날짜 (WP-3 · M3). NULL 이면 아직 살아 있다.
    -- 🔴 `updated_at` 은 벽시각이라 과거 재현에 못 쓴다 — 그래서 이 칸이 있다.
    released_as_of  DATE,
    note            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT inventory_reservations_pkey PRIMARY KEY (reservation_id),
    CONSTRAINT inventory_reservations_sim_run_id_fkey
        FOREIGN KEY (sim_run_id) REFERENCES haetdeul.sim_runs(sim_run_id),
    CONSTRAINT inventory_reservations_item_id_fkey
        FOREIGN KEY (item_id) REFERENCES haetdeul.items(item_id),
    CONSTRAINT inventory_reservations_sale_id_fkey
        FOREIGN KEY (sale_id) REFERENCES haetdeul.sales(sale_id),
    CONSTRAINT ck_inventory_reservations_status
        CHECK (status IN ('RESERVED', 'PARTIALLY_ALLOCATED', 'ALLOCATED', 'RELEASED', 'CANCELLED')),
    -- 확보량이 요구량을 넘을 수 없다 — 넘으면 남의 재고를 잡은 것이다.
    CONSTRAINT ck_inventory_reservations_qty
        CHECK (required_qty_kg > 0 AND reserved_qty_kg >= 0 AND reserved_qty_kg <= required_qty_kg)
);

COMMENT ON TABLE haetdeul.inventory_reservations IS
    '주문 CONFIRMED 시 품목 총량 확보 (02 §11). Lot/Pallet 지정은 Allocation 쪽이다.';

COMMENT ON COLUMN haetdeul.inventory_reservations.released_as_of IS
    '이 예약을 놓아준 시뮬레이션 날짜 (WP-3). NULL = 아직 살아 있다. 🔴 as_of < 이 값인 날에는 여전히 살아 있던 예약이다 — Historical 이 이 칸으로 그날 상태를 유도한다. status 는 지금 값이라 과거 정본이 아니고, updated_at 은 벽시각이라 시뮬레이션 날짜가 아니다.';

COMMIT;
