-- item_storage_policies — 물류
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/10_domain_schema.sql` (2026-08-30 test DB 에서 뜬 pg_dump 스냅샷)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.item_storage_policies (
    item_id text NOT NULL,
    storage_zone text NOT NULL,
    temp_min_c numeric(6,2),
    temp_max_c numeric(6,2),
    rh_min_pct numeric(6,2),
    rh_max_pct numeric(6,2),
    operational_limit_days integer,
    disposal_candidate_days integer DEFAULT 2 NOT NULL,
    medium_grade_factor numeric(8,4) DEFAULT 0.60 NOT NULL,
    loss_rate_baseline numeric(8,6) DEFAULT 0 NOT NULL,
    operational_policy_status text NOT NULL,
    note text
);

COMMENT ON TABLE haetdeul.item_storage_policies IS '품목별 저장환경과 재고 Agent용 운영 보관정책.';

COMMENT ON COLUMN haetdeul.item_storage_policies.item_id IS '품목 고유 ID.';

COMMENT ON COLUMN haetdeul.item_storage_policies.storage_zone IS '보관 Zone 코드.';

COMMENT ON COLUMN haetdeul.item_storage_policies.temp_min_c IS '저장 최저온도(℃).';

COMMENT ON COLUMN haetdeul.item_storage_policies.temp_max_c IS '저장 최고온도(℃).';

COMMENT ON COLUMN haetdeul.item_storage_policies.rh_min_pct IS '상대습도 하한(%).';

COMMENT ON COLUMN haetdeul.item_storage_policies.rh_max_pct IS '상대습도 상한(%).';

COMMENT ON COLUMN haetdeul.item_storage_policies.operational_limit_days IS 'MVP 운영상 상품 등급 보관한계 일수. 공식 유통기한이 아님.';

COMMENT ON COLUMN haetdeul.item_storage_policies.disposal_candidate_days IS '잔여 보관가능일이 이 값 이하이면 폐기검토 후보로 보는 정책값.';

COMMENT ON COLUMN haetdeul.item_storage_policies.medium_grade_factor IS '중품 보관한계 계산 계수. 상품 운영 보관일수×계수.';

COMMENT ON COLUMN haetdeul.item_storage_policies.loss_rate_baseline IS '기본 감모/폐기율.';

COMMENT ON COLUMN haetdeul.item_storage_policies.operational_policy_status IS '운영 보관정책 상태.';

COMMENT ON COLUMN haetdeul.item_storage_policies.note IS '추가 설명 및 주의사항.';

ALTER TABLE ONLY haetdeul.item_storage_policies
    ADD CONSTRAINT item_storage_policies_pkey PRIMARY KEY (item_id);

ALTER TABLE ONLY haetdeul.item_storage_policies
    ADD CONSTRAINT item_storage_policies_item_id_fkey FOREIGN KEY (item_id) REFERENCES haetdeul.items(item_id);

COMMIT;
