-- agent_runs — 공통
--
-- 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.agent_runs (
    agent_run_id text NOT NULL,
    sim_run_id text NOT NULL,
    agent_type text NOT NULL,
    agent_version text,
    as_of date NOT NULL,
    started_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone,
    run_status text NOT NULL,
    input_snapshot_json jsonb,
    output_snapshot_json jsonb,
    error_message text
);

COMMENT ON TABLE haetdeul.agent_runs IS 'Agent 실행 감사로그. 버전·as_of·입출력 Snapshot·오류를 기록한다.';

COMMENT ON COLUMN haetdeul.agent_runs.agent_run_id IS 'Agent 실행 ID.';

COMMENT ON COLUMN haetdeul.agent_runs.sim_run_id IS '시뮬레이션 실행 ID.';

COMMENT ON COLUMN haetdeul.agent_runs.agent_type IS 'Agent 종류.';

COMMENT ON COLUMN haetdeul.agent_runs.agent_version IS 'Agent 버전.';

COMMENT ON COLUMN haetdeul.agent_runs.as_of IS '판단 시점 기준일.';

COMMENT ON COLUMN haetdeul.agent_runs.started_at IS '실행 시작시각.';

COMMENT ON COLUMN haetdeul.agent_runs.finished_at IS '실행 종료시각.';

COMMENT ON COLUMN haetdeul.agent_runs.run_status IS 'Agent 실행상태.';

COMMENT ON COLUMN haetdeul.agent_runs.input_snapshot_json IS 'Agent에 전달된 입력 Snapshot.';

COMMENT ON COLUMN haetdeul.agent_runs.output_snapshot_json IS 'Agent가 생성한 출력 Snapshot.';

COMMENT ON COLUMN haetdeul.agent_runs.error_message IS '실행 실패 시 오류메시지.';

ALTER TABLE ONLY haetdeul.agent_runs
    ADD CONSTRAINT agent_runs_pkey PRIMARY KEY (agent_run_id);

ALTER TABLE ONLY haetdeul.agent_runs
    ADD CONSTRAINT fk_agent_runs_sim_run FOREIGN KEY (sim_run_id) REFERENCES haetdeul.sim_runs(sim_run_id);

COMMIT;
