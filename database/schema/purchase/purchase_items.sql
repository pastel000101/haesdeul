-- purchase_items — 매입
--
-- 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.purchase_items (
    purchase_item_id text NOT NULL,
    purchase_id text NOT NULL,
    item_id text NOT NULL,
    grade text,
    market_name text,
    quantity_kg numeric(18,6) NOT NULL,
    unit_price_krw_per_kg numeric(18,6) NOT NULL,
    line_amount_krw numeric(18,6) NOT NULL,
    source_quote_id text,
    CONSTRAINT purchase_items_check CHECK ((abs((line_amount_krw - (quantity_kg * unit_price_krw_per_kg))) < 0.1)),
    CONSTRAINT purchase_items_line_amount_krw_check CHECK ((line_amount_krw >= (0)::numeric)),
    CONSTRAINT purchase_items_quantity_kg_check CHECK ((quantity_kg >= (0)::numeric)),
    CONSTRAINT purchase_items_unit_price_krw_per_kg_check CHECK ((unit_price_krw_per_kg >= (0)::numeric))
);

COMMENT ON TABLE haetdeul.purchase_items IS '매입 Detail. 품목·등급·시장·수량·단가·Line 금액을 저장한다.';

COMMENT ON COLUMN haetdeul.purchase_items.purchase_item_id IS '매입 Detail ID.';

COMMENT ON COLUMN haetdeul.purchase_items.purchase_id IS '매입 Header ID.';

COMMENT ON COLUMN haetdeul.purchase_items.item_id IS '품목 고유 ID.';

COMMENT ON COLUMN haetdeul.purchase_items.grade IS '품질등급.';

COMMENT ON COLUMN haetdeul.purchase_items.market_name IS '시장/조달처 명칭.';

COMMENT ON COLUMN haetdeul.purchase_items.quantity_kg IS '수량(kg).';

COMMENT ON COLUMN haetdeul.purchase_items.unit_price_krw_per_kg IS '단가(원/kg).';

COMMENT ON COLUMN haetdeul.purchase_items.line_amount_krw IS '매입 Line 금액. quantity_kg×unit_price_krw_per_kg와 일치해야 한다.';

COMMENT ON COLUMN haetdeul.purchase_items.source_quote_id IS '연결된 시장가격 관측 ID.';

ALTER TABLE ONLY haetdeul.purchase_items
    ADD CONSTRAINT purchase_items_pkey PRIMARY KEY (purchase_item_id);

ALTER TABLE ONLY haetdeul.purchase_items
    ADD CONSTRAINT purchase_items_item_id_fkey FOREIGN KEY (item_id) REFERENCES haetdeul.items(item_id);

ALTER TABLE ONLY haetdeul.purchase_items
    ADD CONSTRAINT purchase_items_purchase_id_fkey FOREIGN KEY (purchase_id) REFERENCES haetdeul.purchases(purchase_id) ON DELETE CASCADE;

ALTER TABLE ONLY haetdeul.purchase_items
    ADD CONSTRAINT purchase_items_source_quote_id_fkey FOREIGN KEY (source_quote_id) REFERENCES haetdeul.market_quotes(quote_id);

COMMIT;
