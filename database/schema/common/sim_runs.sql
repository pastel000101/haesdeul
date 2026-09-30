-- sim_runs — 공통
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/10_domain_schema.sql` (2026-08-30 test DB 에서 뜬 pg_dump 스냅샷)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.sim_runs (
    sim_run_id text NOT NULL,
    company_persona_id text NOT NULL,
    run_type text NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    as_of date NOT NULL,
    status text NOT NULL,
    financing_mode text NOT NULL,
    config_json jsonb NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    note text
);

COMMENT ON TABLE haetdeul.sim_runs IS '시뮬레이션/백테스트 최상위 실행 단위. 실행별 Persona와 config를 고정한다.';

COMMENT ON COLUMN haetdeul.sim_runs.sim_run_id IS '시뮬레이션 실행 ID.';

COMMENT ON COLUMN haetdeul.sim_runs.company_persona_id IS '적용 회사 Persona ID.';

COMMENT ON COLUMN haetdeul.sim_runs.run_type IS '시뮬레이션 실행유형.';

COMMENT ON COLUMN haetdeul.sim_runs.period_start IS '실행 기간 시작일.';

COMMENT ON COLUMN haetdeul.sim_runs.period_end IS '실행 기간 종료일.';

COMMENT ON COLUMN haetdeul.sim_runs.as_of IS '판단 시점 기준일.';

COMMENT ON COLUMN haetdeul.sim_runs.status IS '상태값.';

COMMENT ON COLUMN haetdeul.sim_runs.financing_mode IS '자금조달 Scenario.';

COMMENT ON COLUMN haetdeul.sim_runs.config_json IS '실행 당시 Persona/정책/가정을 고정한 설정 Snapshot.';

COMMENT ON COLUMN haetdeul.sim_runs.started_at IS '실행 시작시각.';

COMMENT ON COLUMN haetdeul.sim_runs.finished_at IS '실행 종료시각.';

COMMENT ON COLUMN haetdeul.sim_runs.note IS '추가 설명 및 주의사항.';

ALTER TABLE ONLY haetdeul.sim_runs
    ADD CONSTRAINT sim_runs_pkey PRIMARY KEY (sim_run_id);

ALTER TABLE ONLY haetdeul.sim_runs
    ADD CONSTRAINT sim_runs_company_persona_id_fkey FOREIGN KEY (company_persona_id) REFERENCES haetdeul.company_personas(persona_id);

COMMIT;
