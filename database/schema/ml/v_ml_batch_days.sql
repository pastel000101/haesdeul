-- v_ml_batch_days — ML
--
-- 적용 순서는 `database/new_database_order.txt`.

BEGIN;

-- ── 뷰 ────────────────────────────────────────────────────────────────
--   하루에 한 행. "그날 배치가 어떤 상태였나" 를 넷 중 하나로 답한다.
--
--   주의: 오늘은 아직 안 끝났을 수 있으므로 dt < CURRENT_DATE 까지만 판정하고
--     오늘은 따로 표시한다. 안 끝난 것을 실패로 부르지 않는다
--     (도는 중인 배치를 실패로 단정하지 않기 위해서다).
CREATE OR REPLACE VIEW haetdeul.v_ml_batch_days AS
WITH b AS (
    SELECT base_dt,
           MIN(created_at) AS first_loaded,
           COUNT(*)        AS n_rows
      FROM haetdeul.ml_price_forecasts
     GROUP BY base_dt
)
SELECT c.dt,
       c.is_survey,
       c.is_open,
       c.holiday_nm,
       b.base_dt IS NOT NULL                              AS has_batch,
       b.n_rows,
       (b.first_loaded AT TIME ZONE 'Asia/Seoul')::timestamp(0) AS loaded_kst,
       CASE
           WHEN c.dt = CURRENT_DATE AND b.base_dt IS NULL THEN 'ok'   -- 아직 안 끝남
           WHEN b.base_dt IS NOT NULL
                AND (b.first_loaded AT TIME ZONE 'Asia/Seoul')::date <= c.dt
                                                          THEN 'ok'
           WHEN b.base_dt IS NOT NULL                     THEN 'late'
           WHEN NOT c.is_survey                           THEN 'holiday'
           ELSE 'not_run'
       END                                                AS status
  FROM haetdeul.ml_calendar_days c
  LEFT JOIN b ON b.base_dt = c.dt
 WHERE c.dt <= CURRENT_DATE;

COMMENT ON VIEW haetdeul.v_ml_batch_days IS
  '날마다 예측 배치가 어떤 상태였나. status 값은 ml_batch_day_status 참조. '
  '주 용도는 ML 자신의 감시(not_run 찾기)다. ML 소유';

COMMIT;
