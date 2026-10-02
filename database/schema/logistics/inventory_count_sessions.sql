-- inventory_count_sessions — 물류
--
-- 적용 순서는 `database/new_database_order.txt`.

BEGIN;

-- ═══════════════════════════════════════════════════════════════════════════
-- §8  재고실사
--     표만 정의한다. 실사 Workflow 는 구현되어 있지 않다 — `app/` 에 이 절의 표를 쓰는
--     코드가 없다.
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS haetdeul.inventory_count_sessions (
    count_session_id TEXT NOT NULL,
    sim_run_id       TEXT NOT NULL,
    count_type       TEXT NOT NULL,
    scope_zone_id    TEXT,
    blind_count      BOOLEAN NOT NULL DEFAULT TRUE,
    as_of            DATE NOT NULL,
    started_at       TIMESTAMPTZ NOT NULL,
    finished_at      TIMESTAMPTZ,
    counted_by       TEXT NOT NULL,
    status           TEXT NOT NULL,
    note             TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT inventory_count_sessions_pkey PRIMARY KEY (count_session_id),
    CONSTRAINT inventory_count_sessions_sim_run_id_fkey
        FOREIGN KEY (sim_run_id) REFERENCES haetdeul.sim_runs(sim_run_id),
    CONSTRAINT inventory_count_sessions_scope_zone_id_fkey
        FOREIGN KEY (scope_zone_id) REFERENCES haetdeul.warehouse_zones(zone_id),
    CONSTRAINT ck_inventory_count_sessions_type
        CHECK (count_type IN ('CYCLE', 'FULL', 'AD_HOC')),
    CONSTRAINT ck_inventory_count_sessions_status
        CHECK (status IN ('OPEN', 'COUNTED', 'REVIEWED', 'CLOSED'))
);

COMMENT ON TABLE haetdeul.inventory_count_sessions IS
    '재고실사 회차 (04 §1·§7). 주 1회 순환·월 1회 전체는 법정기준이 아니라 내부 운영가정이다.';

COMMENT ON COLUMN haetdeul.inventory_count_sessions.blind_count IS
    'TRUE = 장부 수량을 먼저 보여주지 않고 실제 확인값을 먼저 입력한다 (04 §4).';

COMMIT;
