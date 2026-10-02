-- v_ml_price_forecast — ML
--
-- 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE VIEW haetdeul.v_ml_price_forecast AS
 SELECT base_dt AS as_of,
    item_nm AS item,
    target_kind,
    to_char((min(generated_at) AT TIME ZONE 'Asia/Seoul'::text), 'YYYY-MM-DD"T"HH24:MI:SS+09:00'::text) AS generated_at,
    min(unit) AS unit,
    min(current_price) AS current_price,
    (count(*))::integer AS horizon_days,
    min(model_version) AS model_version,
    jsonb_agg(jsonb_build_object('date', to_char((target_dt)::timestamp with time zone, 'YYYY-MM-DD'::text), 'predicted', predicted, 'lower', lower, 'upper', upper, 'is_filled', is_filled, 'is_gated', is_gated, 'gate_reason', gate_reason) ORDER BY offset_days) AS daily,
    bool_or(is_filled) AS has_filled_rows,
    (count(*) FILTER (WHERE is_filled))::integer AS filled_count,
    bool_and(COALESCE(use_recommended, true)) AS use_recommended,
    min(quality_note) AS quality_note,
    min(grade_name) AS grade_name,
    min(spec_desc) AS spec_desc
   FROM haetdeul.ml_price_forecasts f
  GROUP BY base_dt, item_nm, target_kind;

COMMENT ON VIEW haetdeul.v_ml_price_forecast IS 'get_forecast 계약 형태. daily 는 D+1~D+18 연속 달력일. has_filled_rows 가 TRUE 면 채운 행이 섞여 있다';

COMMIT;
