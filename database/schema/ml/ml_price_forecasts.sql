-- ml_price_forecasts — ML
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/10_domain_schema.sql` (2026-08-30 test DB 에서 뜬 pg_dump 스냅샷)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE FUNCTION haetdeul.f_ml_forecast_archive() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    reasons TEXT[] := ARRAY[]::TEXT[];
BEGIN
    IF OLD.model_version IS DISTINCT FROM NEW.model_version THEN
        reasons := reasons || 'model'::TEXT;
    END IF;
    IF OLD.generated_at IS DISTINCT FROM NEW.generated_at THEN
        reasons := reasons || 'generated_at'::TEXT;
    END IF;
    IF OLD.predicted IS DISTINCT FROM NEW.predicted
       OR OLD.current_price IS DISTINCT FROM NEW.current_price THEN
        reasons := reasons || 'price'::TEXT;
    END IF;
    IF OLD.lower IS DISTINCT FROM NEW.lower
       OR OLD.upper IS DISTINCT FROM NEW.upper THEN
        reasons := reasons || 'band'::TEXT;
    END IF;
    IF OLD.use_recommended IS DISTINCT FROM NEW.use_recommended THEN
        reasons := reasons || 'quality'::TEXT;
    END IF;
    IF OLD.is_filled IS DISTINCT FROM NEW.is_filled
       OR OLD.is_gated IS DISTINCT FROM NEW.is_gated
       OR OLD.src_lead_biz_d IS DISTINCT FROM NEW.src_lead_biz_d THEN
        reasons := reasons || 'origin'::TEXT;
    END IF;
    IF OLD.spec_desc IS DISTINCT FROM NEW.spec_desc
       OR OLD.grade_name IS DISTINCT FROM NEW.grade_name THEN
        reasons := reasons || 'spec'::TEXT;
    END IF;

    -- 바뀐 게 없으면 이력을 만들지 않는다. 배치를 하루에 여러 번 돌려도
    -- 쓰레기가 쌓이지 않는다.
    IF array_length(reasons, 1) IS NULL THEN
        RETURN NEW;
    END IF;

    INSERT INTO haetdeul.ml_price_forecasts_history (
        change_reason, base_dt, item_nm, target_kind, offset_days, target_dt,
        predicted, lower, upper, current_price, unit, model_version, generated_at,
        src_lead_biz_d, is_filled, is_gated, gate_reason,
        market_name, grade_name, spec_desc, unit_weight_kg,
        quality_note, use_recommended, created_at)
    VALUES (
        array_to_string(reasons, ','), OLD.base_dt, OLD.item_nm, OLD.target_kind,
        OLD.offset_days, OLD.target_dt, OLD.predicted, OLD.lower, OLD.upper,
        OLD.current_price, OLD.unit, OLD.model_version, OLD.generated_at,
        OLD.src_lead_biz_d, OLD.is_filled, OLD.is_gated, OLD.gate_reason,
        OLD.market_name, OLD.grade_name, OLD.spec_desc, OLD.unit_weight_kg,
        OLD.quality_note, OLD.use_recommended, OLD.created_at);
    RETURN NEW;
END $$;

CREATE TABLE haetdeul.ml_price_forecasts (
    base_dt date NOT NULL,
    item_nm text NOT NULL,
    target_kind text NOT NULL,
    offset_days smallint NOT NULL,
    target_dt date NOT NULL,
    predicted integer NOT NULL,
    lower integer NOT NULL,
    upper integer NOT NULL,
    current_price integer NOT NULL,
    unit text NOT NULL,
    model_version text NOT NULL,
    generated_at timestamp with time zone NOT NULL,
    src_lead_biz_d smallint NOT NULL,
    is_filled boolean DEFAULT false NOT NULL,
    is_gated boolean DEFAULT false NOT NULL,
    gate_reason text,
    market_name text,
    grade_name text,
    spec_desc text,
    unit_weight_kg numeric(10,3),
    quality_note text,
    use_recommended boolean,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT ck_ml_price_forecasts_band CHECK (((lower <= predicted) AND (predicted <= upper))),
    CONSTRAINT ck_ml_price_forecasts_calendar_axis CHECK ((target_dt = (base_dt + (offset_days)::integer))),
    CONSTRAINT ck_ml_price_forecasts_kind CHECK ((target_kind = ANY (ARRAY['AUC'::text, 'WHSL'::text, 'RTL'::text]))),
    CONSTRAINT ck_ml_price_forecasts_not_future CHECK ((((generated_at AT TIME ZONE 'Asia/Seoul'::text))::date <= base_dt)),
    CONSTRAINT ck_ml_price_forecasts_offset CHECK (((offset_days >= 1) AND (offset_days <= 18))),
    CONSTRAINT ck_ml_price_forecasts_positive CHECK (((predicted > 0) AND (lower > 0) AND (current_price > 0)))
);

COMMENT ON TABLE haetdeul.ml_price_forecasts IS 'ML 파트 가격 예측. purchase_agent.ports.get_forecast 의 원천. 1행 = 기준일×품목×타겟×D+n(달력일)';

COMMENT ON COLUMN haetdeul.ml_price_forecasts.offset_days IS 'D+1~D+18 **달력일**. 우리 원본은 영업일 축이며 변환은 적재 측(ML)이 책임진다';

COMMENT ON COLUMN haetdeul.ml_price_forecasts.current_price IS '기준일 시점 최신 실제가. 모델이 앵커를 못 이기면 이 값이 곧 답이다';

COMMENT ON COLUMN haetdeul.ml_price_forecasts.is_filled IS 'TRUE = 그날 예측이 없어 직전 개장일 값을 끌어온 행 (토·일·공휴일). 판정에 쓰기 전에 확인할 것';

COMMENT ON COLUMN haetdeul.ml_price_forecasts.is_gated IS 'TRUE = 모델 대신 앵커를 그대로 쓴 행. LT<3 은 어제 가격이 이미 정답에 가까워 모델이 baseline 보다 나쁘다';

ALTER TABLE ONLY haetdeul.ml_price_forecasts
    ADD CONSTRAINT ml_price_forecasts_pkey PRIMARY KEY (base_dt, item_nm, target_kind, offset_days);

CREATE INDEX idx_ml_price_forecasts_kind_item_base ON haetdeul.ml_price_forecasts USING btree (target_kind, item_nm, base_dt DESC);

CREATE TRIGGER trg_ml_forecast_archive BEFORE UPDATE ON haetdeul.ml_price_forecasts FOR EACH ROW EXECUTE FUNCTION haetdeul.f_ml_forecast_archive();

COMMIT;
