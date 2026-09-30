-- partners — 공통
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/10_domain_schema.sql` (2026-08-30 test DB 에서 뜬 pg_dump 스냅샷)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.partners (
    partner_id text NOT NULL,
    partner_name text NOT NULL,
    partner_type text NOT NULL,
    client_type text,
    production_tier text,
    annual_production_ton numeric(12,3),
    operating_days_per_year integer,
    factory_region text,
    factory_city text,
    factory_area text,
    cold_storage_capacity_ton numeric(12,3),
    relationship_days integer,
    order_cycle_days integer,
    sales_collection_days integer,
    pricing_contract_type text,
    active boolean DEFAULT true NOT NULL,
    provisional boolean DEFAULT false NOT NULL,
    note text,
    CONSTRAINT partners_partner_type_check CHECK ((partner_type = ANY (ARRAY['CUSTOMER'::text, 'SUPPLIER'::text, 'LOGISTICS_PROVIDER'::text, 'MARKET_REFERENCE'::text, 'OTHER'::text])))
);

COMMENT ON TABLE haetdeul.partners IS '고객사·공급처·물류사 등 거래 상대방 마스터.';

COMMENT ON COLUMN haetdeul.partners.partner_id IS '거래 상대방 고유 ID.';

COMMENT ON COLUMN haetdeul.partners.partner_name IS '거래 상대방 표시명.';

COMMENT ON COLUMN haetdeul.partners.partner_type IS '거래 상대방 역할 유형.';

COMMENT ON COLUMN haetdeul.partners.client_type IS '고객사 세부 업종 유형.';

COMMENT ON COLUMN haetdeul.partners.production_tier IS '고객사 생산규모 구간.';

COMMENT ON COLUMN haetdeul.partners.annual_production_ton IS '연간 생산량(톤/년).';

COMMENT ON COLUMN haetdeul.partners.operating_days_per_year IS '연간 조업일수.';

COMMENT ON COLUMN haetdeul.partners.factory_region IS '공장 광역지역.';

COMMENT ON COLUMN haetdeul.partners.factory_city IS '공장 시/군.';

COMMENT ON COLUMN haetdeul.partners.factory_area IS '공장 세부지역.';

COMMENT ON COLUMN haetdeul.partners.cold_storage_capacity_ton IS '고객사 자체 저온저장능력. 우리 회사 5PL capacity와 별개.';

COMMENT ON COLUMN haetdeul.partners.relationship_days IS '거래기간(일).';

COMMENT ON COLUMN haetdeul.partners.order_cycle_days IS '기준 발주주기(일).';

COMMENT ON COLUMN haetdeul.partners.sales_collection_days IS 'partners 테이블의 sales_collection_days 값.';

COMMENT ON COLUMN haetdeul.partners.pricing_contract_type IS '가격 계약 방식.';

COMMENT ON COLUMN haetdeul.partners.active IS '활성 여부.';

COMMENT ON COLUMN haetdeul.partners.provisional IS '잠정/Proxy/Assumption 값 포함 여부.';

COMMENT ON COLUMN haetdeul.partners.note IS '추가 설명 및 주의사항.';

ALTER TABLE ONLY haetdeul.partners
    ADD CONSTRAINT partners_pkey PRIMARY KEY (partner_id);

COMMIT;
