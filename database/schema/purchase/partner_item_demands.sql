-- partner_item_demands — 매입
--
-- 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.partner_item_demands (
    partner_id text NOT NULL,
    item_id text NOT NULL,
    daily_demand_kg numeric(14,3) NOT NULL,
    demand_basis text NOT NULL,
    provisional boolean DEFAULT true NOT NULL,
    CONSTRAINT partner_item_demands_daily_demand_kg_check CHECK ((daily_demand_kg >= (0)::numeric))
);

COMMENT ON TABLE haetdeul.partner_item_demands IS '거래처별 품목 일 기준수요를 정규화해 저장하는 관계 테이블.';

COMMENT ON COLUMN haetdeul.partner_item_demands.partner_id IS '거래 상대방 고유 ID.';

COMMENT ON COLUMN haetdeul.partner_item_demands.item_id IS '품목 고유 ID.';

COMMENT ON COLUMN haetdeul.partner_item_demands.daily_demand_kg IS '품목별 일 기준수요(kg/일).';

COMMENT ON COLUMN haetdeul.partner_item_demands.demand_basis IS '수요 산정 근거.';

COMMENT ON COLUMN haetdeul.partner_item_demands.provisional IS '잠정/Proxy/Assumption 값 포함 여부.';

ALTER TABLE ONLY haetdeul.partner_item_demands
    ADD CONSTRAINT partner_item_demands_pkey PRIMARY KEY (partner_id, item_id);

ALTER TABLE ONLY haetdeul.partner_item_demands
    ADD CONSTRAINT partner_item_demands_item_id_fkey FOREIGN KEY (item_id) REFERENCES haetdeul.items(item_id);

ALTER TABLE ONLY haetdeul.partner_item_demands
    ADD CONSTRAINT partner_item_demands_partner_id_fkey FOREIGN KEY (partner_id) REFERENCES haetdeul.partners(partner_id);

COMMIT;
