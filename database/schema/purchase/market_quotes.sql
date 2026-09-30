-- market_quotes — 매입
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/10_domain_schema.sql` (2026-08-30 test DB 에서 뜬 pg_dump 스냅샷)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.market_quotes (
    quote_id text NOT NULL,
    sim_run_id text,
    day_no integer,
    quote_date date NOT NULL,
    item_id text NOT NULL,
    price_type text NOT NULL,
    grade text NOT NULL,
    variety text,
    market_name text,
    quote_method text NOT NULL,
    unit_price_krw_per_kg numeric(18,6) NOT NULL,
    observed_source_date date NOT NULL,
    fill_status text NOT NULL,
    market_observation_count integer,
    evidence_id text,
    CONSTRAINT market_quotes_check CHECK ((observed_source_date <= quote_date)),
    CONSTRAINT market_quotes_price_type_check CHECK ((price_type = ANY (ARRAY['WHOLESALE'::text, 'AUCTION'::text, 'RETAIL'::text]))),
    CONSTRAINT market_quotes_unit_price_krw_per_kg_check CHECK ((unit_price_krw_per_kg >= (0)::numeric))
);

COMMENT ON TABLE haetdeul.market_quotes IS '외부 시장가격 관측 원장. 실제 매입 체결가격과 분리한다.';

COMMENT ON COLUMN haetdeul.market_quotes.quote_id IS '시장가격 관측 고유 ID.';

COMMENT ON COLUMN haetdeul.market_quotes.sim_run_id IS '시뮬레이션 실행 ID.';

COMMENT ON COLUMN haetdeul.market_quotes.day_no IS '시뮬레이션/Burn-in Day 순번.';

COMMENT ON COLUMN haetdeul.market_quotes.quote_date IS '시장가격 적용 기준일.';

COMMENT ON COLUMN haetdeul.market_quotes.item_id IS '품목 고유 ID.';

COMMENT ON COLUMN haetdeul.market_quotes.price_type IS '가격 유형.';

COMMENT ON COLUMN haetdeul.market_quotes.grade IS '품질등급.';

COMMENT ON COLUMN haetdeul.market_quotes.variety IS '품종.';

COMMENT ON COLUMN haetdeul.market_quotes.market_name IS '시장/조달처 명칭.';

COMMENT ON COLUMN haetdeul.market_quotes.quote_method IS '대표가격 산출 방식.';

COMMENT ON COLUMN haetdeul.market_quotes.unit_price_krw_per_kg IS '단가(원/kg).';

COMMENT ON COLUMN haetdeul.market_quotes.observed_source_date IS '실제로 사용된 원천 관측일. look-ahead 방지를 위해 quote_date 이하이어야 한다.';

COMMENT ON COLUMN haetdeul.market_quotes.fill_status IS 'DIRECT 또는 ASOF_PREV_OBSERVATION 등 가격 관측/보정 상태.';

COMMENT ON COLUMN haetdeul.market_quotes.market_observation_count IS '대표가격 산정에 사용한 시장 관측 수.';

COMMENT ON COLUMN haetdeul.market_quotes.evidence_id IS '연결된 근거 원장 ID.';

ALTER TABLE ONLY haetdeul.market_quotes
    ADD CONSTRAINT market_quotes_pkey PRIMARY KEY (quote_id);

ALTER TABLE ONLY haetdeul.market_quotes
    ADD CONSTRAINT fk_market_quotes_sim_run FOREIGN KEY (sim_run_id) REFERENCES haetdeul.sim_runs(sim_run_id);

ALTER TABLE ONLY haetdeul.market_quotes
    ADD CONSTRAINT market_quotes_evidence_id_fkey FOREIGN KEY (evidence_id) REFERENCES haetdeul.evidences(evidence_id);

ALTER TABLE ONLY haetdeul.market_quotes
    ADD CONSTRAINT market_quotes_item_id_fkey FOREIGN KEY (item_id) REFERENCES haetdeul.items(item_id);

COMMIT;
