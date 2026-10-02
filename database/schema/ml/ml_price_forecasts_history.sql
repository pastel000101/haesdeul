-- ml_price_forecasts_history — ML
--
-- 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.ml_price_forecasts_history (
    history_id bigint NOT NULL,
    replaced_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    change_reason text,
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
    is_filled boolean NOT NULL,
    is_gated boolean NOT NULL,
    gate_reason text,
    market_name text,
    grade_name text,
    spec_desc text,
    unit_weight_kg numeric(10,3),
    quality_note text,
    use_recommended boolean,
    created_at timestamp with time zone NOT NULL
);

COMMENT ON TABLE haetdeul.ml_price_forecasts_history IS '덮어쓰기로 대체된 예측. 값이 실제로 바뀔 때만 쌓인다. "그날 무엇을 예측했는지" 재현용';

COMMENT ON COLUMN haetdeul.ml_price_forecasts_history.replaced_at IS '대체된 시각. 이 시각 이전까지 이 행의 값이 유효했다';

COMMENT ON COLUMN haetdeul.ml_price_forecasts_history.change_reason IS '무엇이 바뀌었나 — model / generated_at / price / band / quality 조합';

CREATE SEQUENCE haetdeul.ml_price_forecasts_history_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE haetdeul.ml_price_forecasts_history_history_id_seq OWNED BY haetdeul.ml_price_forecasts_history.history_id;

ALTER TABLE ONLY haetdeul.ml_price_forecasts_history ALTER COLUMN history_id SET DEFAULT nextval('haetdeul.ml_price_forecasts_history_history_id_seq'::regclass);

ALTER TABLE ONLY haetdeul.ml_price_forecasts_history
    ADD CONSTRAINT ml_price_forecasts_history_pkey PRIMARY KEY (history_id);

CREATE INDEX idx_ml_forecast_hist_key ON haetdeul.ml_price_forecasts_history USING btree (base_dt, item_nm, target_kind, offset_days, replaced_at DESC);

CREATE INDEX idx_ml_forecast_hist_replaced ON haetdeul.ml_price_forecasts_history USING btree (replaced_at DESC);

COMMIT;
