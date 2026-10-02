-- inbound_schedules — 물류
--
-- 적용 순서는 `database/new_database_order.txt`.

BEGIN;

-- ═══════════════════════════════════════════════════════════════════════════
-- §3  입고 — 예정 · 도착 · 검수
--     `purchase_items` · `sim_runs` 는 FK 로 가리키기만 한다.
-- ═══════════════════════════════════════════════════════════════════════════

-- ── §3-0  입고 예정 ───────────────────────────────────────────────────────
--
-- 날짜에 안 묶인 업무 Entity 다. 입고 예정을 날짜별 fixture 행에 복제해 두면, 미래 날짜
--    행이 먼저 열린 뒤에 들어온 승인은 승인한 날의 행에만 적혀 도착일에 볼 것이 없다.
--
--    ```text
--    01-15 fixture 가 먼저 열림 (in_transit = [])
--    01-14 승인 → 01-14 행에만 기록
--    01-15 도착일에 볼 것이 없음 → Receipt 0 · Lot 0 · IN Move 0
--    ```
--
--    ⇒ 이 표는 한 번 INSERT 하고 날짜로 질의한다. 미래 날짜 행으로 복제하지
--      않으므로 그런 누락이 구조적으로 생기지 않는다.
--
-- 완료 컬럼이 없다. Receipt 생성과 재고 반영 완료는 다른 사건이고
--    (검수에서 막히면 Receipt 만 선 채로 며칠 간다), 소비자마다 종료점이 다르다.
--
--    ```text
--    운송 중 조회   Receipt 생성 전까지
--    도착 · 용량    Lot + 원장 IN 완료 전까지
--    취소 · 정리    Receipt 0건일 때만
--    ```
--
--    하나를 골라 `COMPLETED` 로 적으면 나머지 소비자가 틀린다. 완료는 downstream
--    사실(`inbound_receipts` · `inventory_lots` · `inventory_moves`)로 유도한다.
--
-- `status` 컬럼이 없다. `cancelled_as_of` 하나가 같은 사실을 말한다 —
--    두 칸을 두면 `status='SCHEDULED'` 인데 `cancelled_as_of` 가 차 있는 모순이
--    성립한다. 한 칸이면 그 자리가 아예 없다.
--
-- ID 를 하나만 든다. `purchase_item_id` 는 `purchase_items` 의 PK 라
--    `purchase_id` · `item_id` · 등급 · 단가를 전부 결정한다. 셋을 따로 저장하면
--    *"purchase_id 는 A 인데 purchase_item_id 는 B 의 줄"* 같은 조합이 만들어지고,
--    낱개 FK 로는 그것을 못 막는다.
--
-- 주의: `purchase_item_id` FK 는 걸지 않는다.
--    `purchase_items → purchases` 가 `ON DELETE CASCADE` 라, 여기에 FK 를 걸면
--    매입 삭제가 과거 재현용 일정까지 함께 지우게 된다. 그 삭제 정책이 정해진
--    적이 없어 기존 동작을 바꾸지 않는다 — 존재·중복은 Writer
--    (`app/logistics/service/transition.py`)가 `purchase_detail.fetch_purchase_detail` 로
--    확인한다(0 / 1 / 2행 이상).
CREATE TABLE IF NOT EXISTS haetdeul.inbound_schedules (
    inbound_id            TEXT NOT NULL,
    sim_run_id            TEXT NOT NULL,
    purchase_item_id      TEXT NOT NULL,
    quantity_kg           NUMERIC(18,6) NOT NULL,
    expected_arrival_date DATE NOT NULL,
    created_as_of         DATE NOT NULL,
    cancelled_as_of       DATE,
    source_ref            TEXT NOT NULL,
    cancel_source_ref     TEXT,
    note                  TEXT,
    recorded_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT inbound_schedules_pkey PRIMARY KEY (sim_run_id, inbound_id),
    CONSTRAINT inbound_schedules_sim_run_id_fkey
        FOREIGN KEY (sim_run_id) REFERENCES haetdeul.sim_runs(sim_run_id),
    CONSTRAINT ck_inbound_schedules_qty CHECK (quantity_kg > 0),
    -- 취소가 생성보다 앞설 수 없다. 리드타임 하한 CHECK 는 걸지 않는다 —
    --    MVP `inbound_lead_days` 정책값(1 calendar day)은 `agent_policy_config` 가
    --    들고 있고, 이 표는 그것으로 이미 계산된 `expected_arrival_date` 를 적을
    --    뿐이라 리드타임 정책을 여기서 다시 검사하지 않는다.
    CONSTRAINT ck_inbound_schedules_cancelled_after_created
        CHECK (cancelled_as_of IS NULL OR cancelled_as_of >= created_as_of)
);

-- 도착 조회의 유일한 접근 경로다 — 날짜별 fixture 를 안 읽는 대신 여기를 읽는다.
CREATE INDEX IF NOT EXISTS idx_inbound_schedules_arrival
    ON haetdeul.inbound_schedules (sim_run_id, expected_arrival_date);

COMMENT ON TABLE haetdeul.inbound_schedules IS
    '입고 예정 업무 Entity (W2.1). 날짜별 fixture JSON 복제를 대체한다 — 한 번 INSERT 하고 날짜로 질의한다. 완료 컬럼이 없다: Receipt 생성과 재고 반영은 다른 사건이고 소비자마다 종료점이 다르다.';

COMMENT ON COLUMN haetdeul.inbound_schedules.inbound_id IS
    '물류가 셈하는 입고 건 (INB-{approval_id}-{seq}). inbound_receipts.inbound_id 와 같은 값이고 그 표에 UNIQUE(sim_run_id, inbound_id) 가 이미 있다.';

COMMENT ON COLUMN haetdeul.inbound_schedules.purchase_item_id IS
    '매입 줄 PK. 🔴 이것 하나가 purchase_id · item_id · grade · 단가를 결정한다 — 그 셋을 여기 복제하지 않는다. FK 는 purchases CASCADE 삭제 정책이 정해질 때까지 보류.';

COMMENT ON COLUMN haetdeul.inbound_schedules.quantity_kg IS
    '이 회차의 입고 예정량. 🔴 purchase_items.quantity_kg 와 같은 값이어야 하는 것이 아니다 — 분할 회차는 매입 줄 하나를 여러 회차로 나눈다.';

COMMENT ON COLUMN haetdeul.inbound_schedules.created_as_of IS
    '이 일정이 장부에 선 시뮬레이션 날짜 (승인 전이의 target_state_date). 🔴 벽시각이 아니다 — 과거 재현이 이 값으로 선다.';

COMMENT ON COLUMN haetdeul.inbound_schedules.cancelled_as_of IS
    'NULL 이면 살아 있다. 값이 있으면 그날부터 취소다 (as_of < cancelled_as_of 인 날에는 여전히 존재). status 컬럼을 따로 두지 않는 이유가 이 한 칸이다.';

COMMENT ON COLUMN haetdeul.inbound_schedules.cancel_source_ref IS
    '입고 일정 취소 근거 (MASTER-CANCEL:{approval_id}@{취소일}). source_ref 는 생성 근거로 그대로 두고 덮지 않는다. 감사용이라 Historical 판정에는 쓰지 않는다 — 그날 살아 있었나는 cancelled_as_of 가 답한다.';

COMMENT ON COLUMN haetdeul.inbound_schedules.recorded_at IS
    '감사용 벽시각. 🔴 Historical 판정에 쓰지 않는다 — 시뮬레이션 날짜는 created_as_of · cancelled_as_of 다.';

COMMIT;
