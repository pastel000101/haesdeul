-- items — 공통
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/10_domain_schema.sql` (2026-08-30 test DB 에서 뜬 pg_dump 스냅샷)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.items (
    item_id text NOT NULL,
    item_code text NOT NULL,
    item_name text NOT NULL,
    category text DEFAULT '채소류'::text NOT NULL,
    base_unit text DEFAULT 'kg'::text NOT NULL,
    mvp_active boolean DEFAULT true NOT NULL,
    ml_target boolean DEFAULT true NOT NULL
);

COMMENT ON TABLE haetdeul.items IS '매입·판매·재고·시장가격·ML이 공통 참조하는 품목 마스터.';

COMMENT ON COLUMN haetdeul.items.item_id IS '품목 고유 ID.';

COMMENT ON COLUMN haetdeul.items.item_code IS 'API/코드에서 사용하는 품목 코드.';

COMMENT ON COLUMN haetdeul.items.item_name IS '품목명.';

COMMENT ON COLUMN haetdeul.items.category IS '분류.';

COMMENT ON COLUMN haetdeul.items.base_unit IS '기본 단위.';

COMMENT ON COLUMN haetdeul.items.mvp_active IS '현재 MVP 대상 여부.';

COMMENT ON COLUMN haetdeul.items.ml_target IS 'ML 예측 대상 여부.';

ALTER TABLE ONLY haetdeul.items
    ADD CONSTRAINT items_item_code_key UNIQUE (item_code);

ALTER TABLE ONLY haetdeul.items
    ADD CONSTRAINT items_item_name_key UNIQUE (item_name);

ALTER TABLE ONLY haetdeul.items
    ADD CONSTRAINT items_pkey PRIMARY KEY (item_id);

COMMIT;
