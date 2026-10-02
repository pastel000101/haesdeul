-- deliveries — 물류
--
-- 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.deliveries (
    delivery_id text NOT NULL,
    sim_run_id text NOT NULL,
    sale_id text NOT NULL,
    customer_partner_id text NOT NULL,
    logistics_contract_id text,
    dispatch_date date NOT NULL,
    delivery_date date NOT NULL,
    total_quantity_kg numeric(18,6) NOT NULL,
    vehicle_class text NOT NULL,
    distance_km numeric(10,3),
    transport_cost_krw numeric(18,6) NOT NULL,
    allocated_logistics_cost_krw numeric(18,6) NOT NULL,
    status text NOT NULL,
    note text,
    CONSTRAINT deliveries_status_check CHECK ((status = ANY (ARRAY['PLANNED'::text, 'DISPATCHED'::text, 'DELIVERED'::text, 'CANCELLED'::text])))
);

COMMENT ON TABLE haetdeul.deliveries IS '판매 건에 대한 실제 배송 실행정보. 현재 5PL/외주운송 기준.';

COMMENT ON COLUMN haetdeul.deliveries.delivery_id IS '배송 ID.';

COMMENT ON COLUMN haetdeul.deliveries.sim_run_id IS '시뮬레이션 실행 ID.';

COMMENT ON COLUMN haetdeul.deliveries.sale_id IS '판매 Header ID.';

COMMENT ON COLUMN haetdeul.deliveries.customer_partner_id IS '판매 고객 Partner ID.';

COMMENT ON COLUMN haetdeul.deliveries.logistics_contract_id IS '적용 물류계약/Persona ID.';

COMMENT ON COLUMN haetdeul.deliveries.dispatch_date IS '배송 출발일.';

COMMENT ON COLUMN haetdeul.deliveries.delivery_date IS '납품일.';

COMMENT ON COLUMN haetdeul.deliveries.total_quantity_kg IS '총수량(kg).';

COMMENT ON COLUMN haetdeul.deliveries.vehicle_class IS '차량 클래스.';

COMMENT ON COLUMN haetdeul.deliveries.distance_km IS '배송거리(km).';

COMMENT ON COLUMN haetdeul.deliveries.transport_cost_krw IS '배송 운송비(원).';

COMMENT ON COLUMN haetdeul.deliveries.allocated_logistics_cost_krw IS '해당 주문에 배부된 직접물류비(원).';

COMMENT ON COLUMN haetdeul.deliveries.status IS '상태값.';

COMMENT ON COLUMN haetdeul.deliveries.note IS '추가 설명 및 주의사항.';

ALTER TABLE ONLY haetdeul.deliveries
    ADD CONSTRAINT deliveries_pkey PRIMARY KEY (delivery_id);

ALTER TABLE ONLY haetdeul.deliveries
    ADD CONSTRAINT deliveries_customer_partner_id_fkey FOREIGN KEY (customer_partner_id) REFERENCES haetdeul.partners(partner_id);

ALTER TABLE ONLY haetdeul.deliveries
    ADD CONSTRAINT deliveries_sale_id_fkey FOREIGN KEY (sale_id) REFERENCES haetdeul.sales(sale_id);

ALTER TABLE ONLY haetdeul.deliveries
    ADD CONSTRAINT fk_deliveries_logistics_contract FOREIGN KEY (logistics_contract_id) REFERENCES haetdeul.logistics_contracts(logistics_contract_id);

ALTER TABLE ONLY haetdeul.deliveries
    ADD CONSTRAINT fk_deliveries_sim_run FOREIGN KEY (sim_run_id) REFERENCES haetdeul.sim_runs(sim_run_id);

COMMIT;
