-- sale_items — 판매
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/10_domain_schema.sql` (2026-08-30 test DB 에서 뜬 pg_dump 스냅샷)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.sale_items (
    sale_item_id text NOT NULL,
    sale_id text NOT NULL,
    item_id text NOT NULL,
    grade text,
    quantity_kg numeric(18,6) NOT NULL,
    unit_price_krw_per_kg numeric(18,6) NOT NULL,
    line_amount_krw numeric(18,6) NOT NULL,
    contribution_profit_krw numeric(18,6) NOT NULL,
    contribution_margin_rate numeric(10,8),
    CONSTRAINT sale_items_check CHECK ((abs((line_amount_krw - (quantity_kg * unit_price_krw_per_kg))) < 0.1)),
    CONSTRAINT sale_items_line_amount_krw_check CHECK ((line_amount_krw >= (0)::numeric)),
    CONSTRAINT sale_items_quantity_kg_check CHECK ((quantity_kg >= (0)::numeric)),
    CONSTRAINT sale_items_unit_price_krw_per_kg_check CHECK ((unit_price_krw_per_kg >= (0)::numeric))
);

COMMENT ON TABLE haetdeul.sale_items IS '판매 Detail. 판매 건에 포함된 품목별 수량·단가·금액·기여이익을 저장한다.';

COMMENT ON COLUMN haetdeul.sale_items.sale_item_id IS '판매 Detail ID.';

COMMENT ON COLUMN haetdeul.sale_items.sale_id IS '판매 Header ID.';

COMMENT ON COLUMN haetdeul.sale_items.item_id IS '품목 고유 ID.';

COMMENT ON COLUMN haetdeul.sale_items.grade IS '품질등급.';

COMMENT ON COLUMN haetdeul.sale_items.quantity_kg IS '수량(kg).';

COMMENT ON COLUMN haetdeul.sale_items.unit_price_krw_per_kg IS '단가(원/kg).';

COMMENT ON COLUMN haetdeul.sale_items.line_amount_krw IS 'sale_items 테이블의 line_amount_krw 값.';

COMMENT ON COLUMN haetdeul.sale_items.contribution_profit_krw IS '기여이익(원).';

COMMENT ON COLUMN haetdeul.sale_items.contribution_margin_rate IS '기여이익률(0~1).';

ALTER TABLE ONLY haetdeul.sale_items
    ADD CONSTRAINT sale_items_pkey PRIMARY KEY (sale_item_id);

ALTER TABLE ONLY haetdeul.sale_items
    ADD CONSTRAINT sale_items_item_id_fkey FOREIGN KEY (item_id) REFERENCES haetdeul.items(item_id);

ALTER TABLE ONLY haetdeul.sale_items
    ADD CONSTRAINT sale_items_sale_id_fkey FOREIGN KEY (sale_id) REFERENCES haetdeul.sales(sale_id) ON DELETE CASCADE;

COMMIT;
