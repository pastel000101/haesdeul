-- inventory_count_sessions — 물류
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/30_logistics_wms_schema.sql` (2026-09-05 실 DB 에서 회수한 WMS 구조)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

-- ═══════════════════════════════════════════════════════════════════════════
-- §8  재고실사
--     ⚠️ 표만 회수한다. 실사 Workflow 구현은 이번 단계 밖이다.
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
