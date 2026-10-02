-- company_personas — 공통
--
-- 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.company_personas (
    persona_id text NOT NULL,
    persona_version text NOT NULL,
    company_name text NOT NULL,
    company_name_en text,
    industry text NOT NULL,
    founder_birth_year integer,
    founder_age integer,
    employment_status text,
    startup_stage text,
    first_startup boolean,
    business_region text,
    company_scale text,
    priority_support boolean,
    tax_arrears boolean,
    credit_incident boolean,
    office_type text,
    office_deposit_krw numeric(18,2) DEFAULT 0 NOT NULL,
    office_monthly_cost_krw numeric(18,2) DEFAULT 0 NOT NULL,
    initial_owner_funding_krw numeric(18,2) NOT NULL,
    initial_debt_krw numeric(18,2) DEFAULT 0 NOT NULL,
    other_external_funding_krw numeric(18,2) DEFAULT 0 NOT NULL,
    target_runway_months integer NOT NULL,
    minimum_cash_buffer_months integer NOT NULL,
    purchase_payment_days integer NOT NULL,
    sales_collection_days integer NOT NULL,
    monthly_labor_cost_krw numeric(18,2) NOT NULL,
    minimum_contribution_margin_rate numeric(10,8),
    target_contribution_margin_rate numeric(10,8),
    burn_in_days integer DEFAULT 30 NOT NULL,
    burn_in_start date,
    burn_in_end date,
    active boolean DEFAULT true NOT NULL,
    note text
);

COMMENT ON TABLE haetdeul.company_personas IS '회사·대표자·재무의 고정 Persona 기준값. 현재 현금처럼 시간에 따라 변하는 값은 finance_states에서 관리한다.';

COMMENT ON COLUMN haetdeul.company_personas.persona_id IS '회사 Persona 고유 ID.';

COMMENT ON COLUMN haetdeul.company_personas.persona_version IS 'Persona 버전.';

COMMENT ON COLUMN haetdeul.company_personas.company_name IS '회사명.';

COMMENT ON COLUMN haetdeul.company_personas.company_name_en IS '회사 영문명.';

COMMENT ON COLUMN haetdeul.company_personas.industry IS '회사 업종.';

COMMENT ON COLUMN haetdeul.company_personas.founder_birth_year IS '대표자 Persona 출생연도.';

COMMENT ON COLUMN haetdeul.company_personas.founder_age IS '기준연도 대표자 Persona 만 나이.';

COMMENT ON COLUMN haetdeul.company_personas.employment_status IS '대표자 Persona 고용상태.';

COMMENT ON COLUMN haetdeul.company_personas.startup_stage IS '창업 단계.';

COMMENT ON COLUMN haetdeul.company_personas.first_startup IS '첫 창업 여부.';

COMMENT ON COLUMN haetdeul.company_personas.business_region IS '사업장 권역.';

COMMENT ON COLUMN haetdeul.company_personas.company_scale IS '기업 규모.';

COMMENT ON COLUMN haetdeul.company_personas.priority_support IS '정책자금 중점지원분야 해당 여부.';

COMMENT ON COLUMN haetdeul.company_personas.tax_arrears IS '세금 체납 여부.';

COMMENT ON COLUMN haetdeul.company_personas.credit_incident IS '금융 연체·사고 여부.';

COMMENT ON COLUMN haetdeul.company_personas.office_type IS '사업장 형태.';

COMMENT ON COLUMN haetdeul.company_personas.office_deposit_krw IS '사무실 보증금(원).';

COMMENT ON COLUMN haetdeul.company_personas.office_monthly_cost_krw IS '사무실 월 비용(원/월).';

COMMENT ON COLUMN haetdeul.company_personas.initial_owner_funding_krw IS '초기 자기자금(원).';

COMMENT ON COLUMN haetdeul.company_personas.initial_debt_krw IS 'Day0 초기 부채(원).';

COMMENT ON COLUMN haetdeul.company_personas.other_external_funding_krw IS '기타 외부조달금(원).';

COMMENT ON COLUMN haetdeul.company_personas.target_runway_months IS '목표 운영기간(개월).';

COMMENT ON COLUMN haetdeul.company_personas.minimum_cash_buffer_months IS '최소 현금 방어기간(개월).';

COMMENT ON COLUMN haetdeul.company_personas.purchase_payment_days IS '매입대금 지급주기 D+n. 현재 D+0 Baseline이며 실계약 확인 시 교체 대상.';

COMMENT ON COLUMN haetdeul.company_personas.sales_collection_days IS '판매대금 회수주기 D+n. 현재 통합 Persona 기준 D+30.';

COMMENT ON COLUMN haetdeul.company_personas.monthly_labor_cost_krw IS '월 인건비 기준값(원/월).';

COMMENT ON COLUMN haetdeul.company_personas.minimum_contribution_margin_rate IS '손익분기 관점의 최소 기여이익률. 0~1 비율.';

COMMENT ON COLUMN haetdeul.company_personas.target_contribution_margin_rate IS '영업 가격정책 목표 기여이익률. 0~1 비율.';

COMMENT ON COLUMN haetdeul.company_personas.burn_in_days IS 'Agent 실행 전 선행운영 기간(일).';

COMMENT ON COLUMN haetdeul.company_personas.burn_in_start IS 'Burn-in 시작일.';

COMMENT ON COLUMN haetdeul.company_personas.burn_in_end IS 'Burn-in 종료일.';

COMMENT ON COLUMN haetdeul.company_personas.active IS '활성 여부.';

COMMENT ON COLUMN haetdeul.company_personas.note IS '추가 설명 및 주의사항.';

ALTER TABLE ONLY haetdeul.company_personas
    ADD CONSTRAINT company_personas_persona_version_key UNIQUE (persona_version);

ALTER TABLE ONLY haetdeul.company_personas
    ADD CONSTRAINT company_personas_pkey PRIMARY KEY (persona_id);

COMMIT;
