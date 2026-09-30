-- ml_calendar_days — ML
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/ml_calendar_days.sql` (2026-09-04 ML 달력)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

-- ============================================================================
-- haetdeul.ml_calendar_days · v_ml_batch_days   (2026-09-04)
--
-- ▣ 왜 만드나
--    "당일 예측 배치가 없다" 는 사실 하나에 **세 가지 다른 뜻**이 섞여 있다.
--
--        공휴일·주말   정상 — 알릴 필요 없다
--        ML 미실행     비정상 — ML 이 알아야 한다
--        적재 지연     비정상 — push 시각 문제
--
--    지금은 셋이 같은 문장으로 나간다. 마스터가 `app/master/inputs.py` 에
--    *"원인을 단정하지 않는다 … 마스터는 그것을 구분할 수 없다"* 고 적어 둔
--    자리다. 구분할 재료(조사일·경매일·공휴일)를 가진 것은 ML 뿐이다.
--
-- ▣ 주 용도는 **ML 자신의 감시**다
--    마스터는 당장 안 읽는다 — 공휴일이든 미실행이든 그날 매입안은 못 내므로
--    판정이 안 바뀐다. **판정이 안 바뀌는 값을 판정 경로에 넣지 않는다.**
--    `not_run` 을 알아야 하는 것은 ML 이다.
--
-- ▣ 소유
--    **ML 이 만들고 ML 이 채운다.** 매일 배치가 ref_calendar 에서 밀어 넣는다.
--    다른 파트는 읽기만 한다.
--
-- ▣ 실행
--    이 파일은 haetdeul 스키마가 있는 DB(매입 파트 DB)에서 돌린다.
--    우리 DB 가 아니다.
-- ============================================================================

-- ── 어휘를 표로 박는다 ────────────────────────────────────────────────
--   뷰에는 CHECK 를 걸 수 없으므로 값의 목록을 표로 둔다.
--   다섯째 값이 필요해지면 여기에 먼저 넣는다.

BEGIN;

-- ── 달력 ──────────────────────────────────────────────────────────────
--   우리 ref_calendar 를 그대로 옮긴다. 매일 배치가 갱신한다.
--
--   ★ 축이 둘이다 (CLAUDE.md 5.8).
--       is_survey  중도매·소매 조사일. **기준일(base_dt)은 이 축에서 나온다**
--       is_open    가락 경매일. 토요일은 여기만 참이다 (2026년 45일)
CREATE TABLE IF NOT EXISTS haetdeul.ml_calendar_days (
    dt          DATE PRIMARY KEY,
    is_survey   BOOLEAN     NOT NULL,
    is_open     BOOLEAN     NOT NULL,
    holiday_nm  TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE haetdeul.ml_calendar_days IS
  '조사일·경매일·공휴일 달력. ML 이 만들고 ML 이 채운다 (ref_calendar 사본). '
  '다른 파트는 읽기만 한다';

COMMENT ON COLUMN haetdeul.ml_calendar_days.is_survey IS
  '중도매·소매 조사일. 예측 기준일(base_dt)이 이 축에서 나온다. 토요일은 거짓';

COMMENT ON COLUMN haetdeul.ml_calendar_days.is_open IS
  '가락시장 경매일. 토요일도 참이다 (2026년 45일 · 물량 13.7%)';

COMMIT;
