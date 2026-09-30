-- logistics_contracts — 물류
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/10_domain_schema.sql` (2026-08-30 test DB 에서 뜬 pg_dump 스냅샷)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.logistics_contracts (
    logistics_contract_id text NOT NULL,
    company_persona_id text NOT NULL,
    provider_partner_id text,
    logistics_model text NOT NULL,
    own_warehouse boolean NOT NULL,
    own_vehicle_count integer NOT NULL,
    required_capacity_plt numeric(12,3) NOT NULL,
    guaranteed_capacity_plt numeric(12,3) NOT NULL,
    effective_kg_per_pallet numeric(18,3) NOT NULL,
    equivalent_capacity_ton numeric(18,6) NOT NULL,
    storage_rate_per_plt_month_krw numeric(18,6) NOT NULL,
    handling_rate_per_plt_event_krw numeric(18,6) NOT NULL,
    vehicle_class text NOT NULL,
    delivery_distance_km numeric(10,3),
    transport_cost_per_delivery_krw numeric(18,6) NOT NULL,
    management_fee_rate numeric(10,8) NOT NULL,
    monthly_storage_cost_krw numeric(18,6) NOT NULL,
    monthly_handling_cost_krw numeric(18,6) NOT NULL,
    monthly_transport_cost_krw numeric(18,6) NOT NULL,
    monthly_management_fee_krw numeric(18,6) NOT NULL,
    monthly_total_logistics_cost_krw numeric(18,6) NOT NULL,
    safety_stock_ratio numeric(10,8) NOT NULL,
    capacity_safety_margin_rate numeric(10,8) NOT NULL,
    contract_status text NOT NULL,
    provisional boolean DEFAULT true NOT NULL,
    note text
);

COMMENT ON TABLE haetdeul.logistics_contracts IS '5PL 용량·단가·차량·수수료 등 물류 Persona/계약 기준.';

COMMENT ON COLUMN haetdeul.logistics_contracts.logistics_contract_id IS '적용 물류계약/Persona ID.';

COMMENT ON COLUMN haetdeul.logistics_contracts.company_persona_id IS '적용 회사 Persona ID.';

COMMENT ON COLUMN haetdeul.logistics_contracts.provider_partner_id IS '물류 공급자 Partner ID.';

COMMENT ON COLUMN haetdeul.logistics_contracts.logistics_model IS '물류 운영모델.';

COMMENT ON COLUMN haetdeul.logistics_contracts.own_warehouse IS '자가창고 보유 여부.';

COMMENT ON COLUMN haetdeul.logistics_contracts.own_vehicle_count IS '자가차량 보유대수.';

COMMENT ON COLUMN haetdeul.logistics_contracts.required_capacity_plt IS '기본 필요 Pallet 수.';

COMMENT ON COLUMN haetdeul.logistics_contracts.guaranteed_capacity_plt IS '시뮬레이션에서 보장되는 것으로 보는 5PL Pallet 수. 실 SLA 확정 전 Baseline.';

COMMENT ON COLUMN haetdeul.logistics_contracts.effective_kg_per_pallet IS '운영상 1PLT당 유효 적재중량(kg).';

COMMENT ON COLUMN haetdeul.logistics_contracts.equivalent_capacity_ton IS '보장 PLT×유효 적재중량을 톤으로 환산한 용량.';

COMMENT ON COLUMN haetdeul.logistics_contracts.storage_rate_per_plt_month_krw IS '보관단가(원/PLT·월).';

COMMENT ON COLUMN haetdeul.logistics_contracts.handling_rate_per_plt_event_krw IS '하역단가(원/PLT·event).';

COMMENT ON COLUMN haetdeul.logistics_contracts.vehicle_class IS '차량 클래스.';

COMMENT ON COLUMN haetdeul.logistics_contracts.delivery_distance_km IS '배송비 산정 기준거리(km).';

COMMENT ON COLUMN haetdeul.logistics_contracts.transport_cost_per_delivery_krw IS '배송 1회 운송비 기준값(원).';

COMMENT ON COLUMN haetdeul.logistics_contracts.management_fee_rate IS '5PL 관리수수료율. 0~1 비율이며 현재 Simulation Assumption.';

COMMENT ON COLUMN haetdeul.logistics_contracts.monthly_storage_cost_krw IS '월 보관비(원).';

COMMENT ON COLUMN haetdeul.logistics_contracts.monthly_handling_cost_krw IS '월 하역비(원).';

COMMENT ON COLUMN haetdeul.logistics_contracts.monthly_transport_cost_krw IS '월 운송비(원).';

COMMENT ON COLUMN haetdeul.logistics_contracts.monthly_management_fee_krw IS '월 5PL 관리수수료(원).';

COMMENT ON COLUMN haetdeul.logistics_contracts.monthly_total_logistics_cost_krw IS '월 총 물류비(원).';

COMMENT ON COLUMN haetdeul.logistics_contracts.safety_stock_ratio IS '안전재고 비율(0~1).';

COMMENT ON COLUMN haetdeul.logistics_contracts.capacity_safety_margin_rate IS '물류 capacity 판단 안전마진(0~1).';

COMMENT ON COLUMN haetdeul.logistics_contracts.contract_status IS '계약/Simulation 상태.';

COMMENT ON COLUMN haetdeul.logistics_contracts.provisional IS '잠정/Proxy/Assumption 값 포함 여부.';

COMMENT ON COLUMN haetdeul.logistics_contracts.note IS '추가 설명 및 주의사항.';

ALTER TABLE ONLY haetdeul.logistics_contracts
    ADD CONSTRAINT logistics_contracts_pkey PRIMARY KEY (logistics_contract_id);

ALTER TABLE ONLY haetdeul.logistics_contracts
    ADD CONSTRAINT logistics_contracts_company_persona_id_fkey FOREIGN KEY (company_persona_id) REFERENCES haetdeul.company_personas(persona_id);

ALTER TABLE ONLY haetdeul.logistics_contracts
    ADD CONSTRAINT logistics_contracts_provider_partner_id_fkey FOREIGN KEY (provider_partner_id) REFERENCES haetdeul.partners(partner_id);

COMMIT;
