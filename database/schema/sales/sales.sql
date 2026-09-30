-- sales — 판매
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/10_domain_schema.sql` (2026-08-30 test DB 에서 뜬 pg_dump 스냅샷)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.
-- 2026-09-30 BL-021 보완: 새 DB 목록 끝에서 따로 돌던 이관판의 최종 효과를 이 파일에 담았다 —
--   `migrations/sales/sales_source_order_id_nullable.sql` 의 `source_order_id` NOT NULL 해제.
--   그 칸 정의만 스냅샷과 다르다. 이미 쓰는 DB 는 그 이관판을 그대로 쓴다(`database/README.md` §2).

BEGIN;

CREATE TABLE haetdeul.sales (
    sale_id text NOT NULL,
    sim_run_id text NOT NULL,
    customer_partner_id text NOT NULL,
    order_date date NOT NULL,
    sale_date date NOT NULL,
    collection_due_date date NOT NULL,
    total_quantity_kg numeric(18,6) NOT NULL,
    total_amount_krw numeric(18,6) NOT NULL,
    contribution_profit_krw numeric(18,6) NOT NULL,
    collection_status text NOT NULL,
    source_order_id text,
    note text,
    order_status character varying(30) DEFAULT 'DELIVERED'::character varying NOT NULL,
    CONSTRAINT sales_collection_status_check CHECK ((collection_status = ANY (ARRAY['OPEN'::text, 'PARTIAL'::text, 'COLLECTED'::text, 'CANCELLED'::text]))),
    CONSTRAINT sales_order_status_check CHECK (((order_status)::text = ANY ((ARRAY['CONFIRMED'::character varying, 'READY'::character varying, 'DELIVERED'::character varying, 'CANCELLED'::character varying])::text[])))
);

COMMENT ON TABLE haetdeul.sales IS '판매/주문 Header. 거래처·일자·총수량·총금액·기여이익·회수상태를 저장한다.';

COMMENT ON COLUMN haetdeul.sales.sale_id IS '판매 Header ID.';

COMMENT ON COLUMN haetdeul.sales.sim_run_id IS '시뮬레이션 실행 ID.';

COMMENT ON COLUMN haetdeul.sales.customer_partner_id IS '판매 고객 Partner ID.';

COMMENT ON COLUMN haetdeul.sales.order_date IS '고객 주문일.';

COMMENT ON COLUMN haetdeul.sales.sale_date IS '판매/납품 기준일.';

COMMENT ON COLUMN haetdeul.sales.collection_due_date IS '판매대금 회수예정일.';

COMMENT ON COLUMN haetdeul.sales.total_quantity_kg IS '판매 Header 총수량. sale_items.quantity_kg 합계와 일치해야 한다.';

COMMENT ON COLUMN haetdeul.sales.total_amount_krw IS '판매 Header 총금액. sale_items.line_amount_krw 합계와 일치해야 한다.';

COMMENT ON COLUMN haetdeul.sales.contribution_profit_krw IS '기여이익(원).';

COMMENT ON COLUMN haetdeul.sales.collection_status IS '판매대금 회수상태.';

COMMENT ON COLUMN haetdeul.sales.source_order_id IS '원본 주문 ID.';

COMMENT ON COLUMN haetdeul.sales.note IS '추가 설명 및 주의사항.';

ALTER TABLE ONLY haetdeul.sales
    ADD CONSTRAINT sales_pkey PRIMARY KEY (sale_id);

ALTER TABLE ONLY haetdeul.sales
    ADD CONSTRAINT fk_sales_sim_run FOREIGN KEY (sim_run_id) REFERENCES haetdeul.sim_runs(sim_run_id);

ALTER TABLE ONLY haetdeul.sales
    ADD CONSTRAINT sales_customer_partner_id_fkey FOREIGN KEY (customer_partner_id) REFERENCES haetdeul.partners(partner_id);

COMMIT;
