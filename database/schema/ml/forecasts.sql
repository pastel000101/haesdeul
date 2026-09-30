-- forecasts — ML
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/10_domain_schema.sql` (2026-08-30 test DB 에서 뜬 pg_dump 스냅샷)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.forecasts (
    forecast_id text NOT NULL,
    sim_run_id text,
    item_id text NOT NULL,
    generated_at timestamp with time zone NOT NULL,
    as_of date NOT NULL,
    target_date date NOT NULL,
    price_type text NOT NULL,
    predicted_price_krw_per_kg numeric(18,6) NOT NULL,
    low_price_krw_per_kg numeric(18,6),
    high_price_krw_per_kg numeric(18,6),
    confidence numeric(8,6),
    model_version text NOT NULL,
    evidence_id text,
    CONSTRAINT forecasts_check CHECK (((generated_at)::date <= as_of)),
    CONSTRAINT forecasts_check1 CHECK ((target_date >= as_of))
);

COMMENT ON TABLE haetdeul.forecasts IS 'ML 가격예측 결과. 실제 ML Pipeline이 생성하며 초기 Seed에서는 비어 있는 것이 정상이다.';

COMMENT ON COLUMN haetdeul.forecasts.forecast_id IS 'ML 예측 고유 ID.';

COMMENT ON COLUMN haetdeul.forecasts.sim_run_id IS '시뮬레이션 실행 ID.';

COMMENT ON COLUMN haetdeul.forecasts.item_id IS '품목 고유 ID.';

COMMENT ON COLUMN haetdeul.forecasts.generated_at IS '모델 예측 생성시각. generated_at::date는 as_of 이후일 수 없다.';

COMMENT ON COLUMN haetdeul.forecasts.as_of IS '모델이 사용할 수 있었던 정보의 판단 기준일.';

COMMENT ON COLUMN haetdeul.forecasts.target_date IS '예측 대상일.';

COMMENT ON COLUMN haetdeul.forecasts.price_type IS '가격 유형.';

COMMENT ON COLUMN haetdeul.forecasts.predicted_price_krw_per_kg IS '예측가격(원/kg).';

COMMENT ON COLUMN haetdeul.forecasts.low_price_krw_per_kg IS '예측구간 하한(원/kg).';

COMMENT ON COLUMN haetdeul.forecasts.high_price_krw_per_kg IS '예측구간 상한(원/kg).';

COMMENT ON COLUMN haetdeul.forecasts.confidence IS '신뢰도/불확실성 정보.';

COMMENT ON COLUMN haetdeul.forecasts.model_version IS 'ML 모델 버전.';

COMMENT ON COLUMN haetdeul.forecasts.evidence_id IS '연결된 근거 원장 ID.';

ALTER TABLE ONLY haetdeul.forecasts
    ADD CONSTRAINT forecasts_pkey PRIMARY KEY (forecast_id);

ALTER TABLE ONLY haetdeul.forecasts
    ADD CONSTRAINT fk_forecasts_sim_run FOREIGN KEY (sim_run_id) REFERENCES haetdeul.sim_runs(sim_run_id);

ALTER TABLE ONLY haetdeul.forecasts
    ADD CONSTRAINT forecasts_evidence_id_fkey FOREIGN KEY (evidence_id) REFERENCES haetdeul.evidences(evidence_id);

ALTER TABLE ONLY haetdeul.forecasts
    ADD CONSTRAINT forecasts_item_id_fkey FOREIGN KEY (item_id) REFERENCES haetdeul.items(item_id);

COMMIT;
