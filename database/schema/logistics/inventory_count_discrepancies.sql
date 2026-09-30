-- inventory_count_discrepancies — 물류
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/30_logistics_wms_schema.sql` (2026-09-05 실 DB 에서 회수한 WMS 구조)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE IF NOT EXISTS haetdeul.inventory_count_discrepancies (
    discrepancy_id     TEXT NOT NULL,
    count_line_id      BIGINT NOT NULL,
    discrepancy_type   TEXT NOT NULL,
    system_qty_kg      NUMERIC(18,6),
    physical_qty_kg    NUMERIC(18,6),
    variance_qty_kg    NUMERIC(18,6),
    cause_candidates   JSONB,
    recommended_action TEXT NOT NULL,
    resolution_status  TEXT NOT NULL,
    resolved_move_id   TEXT,
    approved_by        TEXT,
    approved_at        TIMESTAMPTZ,
    note               TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT inventory_count_discrepancies_pkey PRIMARY KEY (discrepancy_id),
    CONSTRAINT inventory_count_discrepancies_count_line_id_fkey
        FOREIGN KEY (count_line_id) REFERENCES haetdeul.inventory_count_lines(count_line_id),
    CONSTRAINT inventory_count_discrepancies_resolved_move_id_fkey
        FOREIGN KEY (resolved_move_id) REFERENCES haetdeul.inventory_moves(move_id),
    CONSTRAINT ck_count_discrepancy_type
        CHECK (discrepancy_type IN ('QTY_MISMATCH', 'PALLET_NOT_FOUND',
                                    'UNEXPECTED_PALLET', 'LOCATION_MISMATCH')),
    CONSTRAINT ck_count_discrepancy_action
        CHECK (recommended_action IN ('RECOUNT', 'ADJUST_IN', 'ADJUST_OUT',
                                      'HOLD', 'RELOCATE', 'NONE')),
    CONSTRAINT ck_count_discrepancy_status
        CHECK (resolution_status IN ('OPEN', 'RECOUNT_REQUESTED', 'APPROVED', 'REJECTED', 'CLOSED')),
    -- 승인 없는 장부수정은 없다 — 조정 Move 가 붙으려면 승인자·승인시각이 있어야 한다.
    CONSTRAINT ck_count_discrepancy_approval
        CHECK (resolved_move_id IS NULL
               OR (approved_by IS NOT NULL AND approved_at IS NOT NULL
                   AND resolution_status = 'APPROVED'))
);

CREATE INDEX IF NOT EXISTS idx_count_discrepancies_open
    ON haetdeul.inventory_count_discrepancies (resolution_status)
    WHERE resolution_status IN ('OPEN', 'RECOUNT_REQUESTED');

COMMENT ON TABLE haetdeul.inventory_count_discrepancies IS
    '장부/실사 불일치와 처리 (04 §5·§6). AI 는 원인 후보와 조치를 추천만 하고, 승인 없는 장부수정은 없다 (04 §8).';

COMMENT ON COLUMN haetdeul.inventory_count_discrepancies.cause_candidates IS
    'Agent 가 제시한 원인 후보. 추천이지 확정 사실이 아니다.';

COMMIT;
