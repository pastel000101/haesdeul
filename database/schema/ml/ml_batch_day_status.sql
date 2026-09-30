-- ml_batch_day_status — ML
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/ml_calendar_days.sql` (2026-09-04 ML 달력)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE IF NOT EXISTS haetdeul.ml_batch_day_status (
    code  TEXT PRIMARY KEY
          CHECK (code IN ('ok', 'holiday', 'not_run', 'late')),
    note  TEXT NOT NULL
);

COMMENT ON TABLE haetdeul.ml_batch_day_status IS
  'v_ml_batch_days.status 가 가질 수 있는 값. ML 소유';

INSERT INTO haetdeul.ml_batch_day_status (code, note) VALUES
  ('ok',      '그날 기준일 예측이 그날 안에 들어왔다'),
  ('holiday', '조사일이 아니라 원래 배치가 없다 — 정상'),
  ('not_run', '조사일인데 배치가 없다 — ML 이 봐야 한다'),
  ('late',    '배치는 있는데 그날 안에 안 들어왔다')
ON CONFLICT (code) DO UPDATE SET note = EXCLUDED.note;

COMMIT;
