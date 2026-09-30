-- item_turnover_policies — 물류
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/30_logistics_wms_schema.sql` (2026-09-05 실 DB 에서 회수한 WMS 구조)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE IF NOT EXISTS haetdeul.item_turnover_policies (
    item_id                          TEXT NOT NULL,
    operational_turnover_target_days INTEGER NOT NULL,
    sell_priority_remaining_days     INTEGER NOT NULL,
    turnover_clock_start             TEXT NOT NULL DEFAULT 'received_at',
    physical_storage_limit_days      INTEGER,
    physical_storage_limit_status    TEXT NOT NULL DEFAULT 'NOT_FIXED',
    policy_status                    TEXT NOT NULL,
    evidence_grade                   TEXT NOT NULL,
    source_ref                       TEXT NOT NULL,
    note                             TEXT,
    created_at                       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT item_turnover_policies_pkey PRIMARY KEY (item_id),
    CONSTRAINT item_turnover_policies_item_id_fkey
        FOREIGN KEY (item_id) REFERENCES haetdeul.items(item_id),
    CONSTRAINT ck_item_turnover_target CHECK (operational_turnover_target_days > 0),
    CONSTRAINT ck_item_turnover_sell_priority
        CHECK (sell_priority_remaining_days >= 0
               AND sell_priority_remaining_days <= operational_turnover_target_days),
    CONSTRAINT ck_item_turnover_clock_start
        CHECK (turnover_clock_start IN ('received_at', 'harvest_date')),
    -- 미확정을 숫자로 지어내지 못하게 상태와 값을 한 쌍으로 묶는다.
    CONSTRAINT ck_item_turnover_physical_limit
        CHECK ((physical_storage_limit_status = 'NOT_FIXED' AND physical_storage_limit_days IS NULL)
               OR (physical_storage_limit_status = 'FIXED' AND physical_storage_limit_days > 0)),
    CONSTRAINT ck_item_turnover_policy_status
        CHECK (policy_status IN ('SIMULATION_POLICY', 'CONFIRMED_POLICY')),
    CONSTRAINT ck_item_turnover_grade
        CHECK (evidence_grade IN ('OFFICIAL', 'VENDOR', 'SIM_FIXED', 'ASSUMED'))
);

COMMENT ON TABLE haetdeul.item_turnover_policies IS
    '신규 회전목표 계약 (Persona 05). Legacy item_storage_policies 를 대체하지 않고 병행한다 — 판매·매입 전환 완료 전 Legacy 제거 금지 (07 §8). 🔴 3품목만 있다 — Lot 조회에서 INNER JOIN 하면 계약 밖 품목 재고가 사라진다 (아래 주석).';

COMMENT ON COLUMN haetdeul.item_turnover_policies.operational_turnover_target_days IS
    '재고회전 목표일수. 실제 부패기한도 판매불가기한도 아니다 (05 §1·§2).';

COMMENT ON COLUMN haetdeul.item_turnover_policies.sell_priority_remaining_days IS
    '이 잔여일 이하에서 SELL_PRIORITY Signal 을 낸다. 자동 할인·자동 가격조정이 아니다 (05 §7.1).';

COMMENT ON COLUMN haetdeul.item_turnover_policies.physical_storage_limit_days IS
    '물리 저장한계(실제 Shelf-Life). 회전목표와 숫자가 같아도 뜻이 다르다 — 아직 미확정이다 (05 §8.1).';

COMMIT;
