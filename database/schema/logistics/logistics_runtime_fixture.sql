-- logistics_runtime_fixture — 물류
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/10_domain_schema.sql` (2026-08-30 test DB 에서 뜬 pg_dump 스냅샷)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.
-- 2026-09-30 BL-021 보완: 새 DB 목록 끝에서 따로 돌던 이관판의 최종 효과를 이 파일에 담았다 —
--   `migrations/logistics/logistics_drop_inbound_json.sql`. 스냅샷의 입고 JSON 두 칸
--   (`in_transit_json` · `confirmed_inbound_json`)은 새 DB 에서 처음부터 만들지 않고, 상태 두 칸에
--   그 이관판의 칸 주석을 단다. 입고 예정의 정본은 `inbound_schedules` 다.
--   이미 쓰는 DB 는 그 이관판을 그대로 쓴다(`database/README.md` §2 — 적용 전 확인 넷은 그 머리말).

BEGIN;

CREATE TABLE haetdeul.logistics_runtime_fixture (
    fixture_id text NOT NULL,
    sim_run_id text NOT NULL,
    as_of date NOT NULL,
    in_transit_status text NOT NULL,
    confirmed_inbound_status text NOT NULL,
    confirmed_outbound_status text NOT NULL,
    confirmed_outbound_json jsonb,
    usage_scope text NOT NULL,
    evidence_grade text NOT NULL,
    source_ref text NOT NULL,
    approved_by text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    lot_priority_status text,
    lot_priority_json jsonb,
    zone_capacity_status text,
    guaranteed_capacity_by_zone_json jsonb,
    CONSTRAINT ck_log_runtime_in_transit_status CHECK ((in_transit_status = ANY (ARRAY['CONFIRMED'::text, 'CONFIRMED_ZERO'::text, 'UNRESOLVED'::text]))),
    CONSTRAINT ck_log_runtime_inbound_status CHECK ((confirmed_inbound_status = ANY (ARRAY['CONFIRMED'::text, 'CONFIRMED_ZERO'::text, 'UNRESOLVED'::text]))),
    CONSTRAINT ck_log_runtime_outbound_status CHECK ((confirmed_outbound_status = ANY (ARRAY['CONFIRMED'::text, 'CONFIRMED_ZERO'::text, 'UNRESOLVED'::text]))),
    CONSTRAINT ck_logistics_runtime_fixture_lot_priority_status CHECK (((lot_priority_status IS NULL) OR (lot_priority_status = ANY (ARRAY['CONFIRMED'::text, 'CONFIRMED_ZERO'::text, 'UNRESOLVED'::text])))),
    CONSTRAINT ck_logistics_runtime_fixture_zone_capacity_status CHECK (((zone_capacity_status IS NULL) OR (zone_capacity_status = ANY (ARRAY['CONFIRMED'::text, 'CONFIRMED_ZERO'::text, 'UNRESOLVED'::text]))))
);

COMMENT ON COLUMN haetdeul.logistics_runtime_fixture.in_transit_status IS
    '운송 중 축을 확인했나. UNRESOLVED = 확인한 적 없음(Reader 가 목록을 숨긴다) · 그 밖 = inbound_schedules 가 목록을 정한다. 🔴 이 칸이 목록을 들지 않는다.';
COMMENT ON COLUMN haetdeul.logistics_runtime_fixture.confirmed_inbound_status IS
    '확정 입고 축을 확인했나. 의미는 in_transit_status 와 같다 — 목록의 정본은 inbound_schedules 다.';

ALTER TABLE ONLY haetdeul.logistics_runtime_fixture
    ADD CONSTRAINT logistics_runtime_fixture_pkey PRIMARY KEY (fixture_id);

ALTER TABLE ONLY haetdeul.logistics_runtime_fixture
    ADD CONSTRAINT uq_log_runtime_fixture UNIQUE (sim_run_id, as_of, usage_scope);

COMMIT;
