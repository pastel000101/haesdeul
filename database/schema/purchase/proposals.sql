-- proposals — 매입
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/10_domain_schema.sql` (2026-08-30 test DB 에서 뜬 pg_dump 스냅샷)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.proposals (
    proposal_id text NOT NULL,
    scenario_id text NOT NULL,
    sim_run_id text NOT NULL,
    purchase_agent_run_id text,
    as_of date NOT NULL,
    item_id text,
    label text NOT NULL,
    total_quantity_kg numeric(18,6),
    max_price_krw_per_kg numeric(18,6),
    timing text,
    split_plan_json jsonb,
    sourcing_plan_json jsonb,
    expected_margin_rate numeric(10,8),
    expected_cost_krw numeric(18,6),
    confidence text,
    situation text,
    status text NOT NULL,
    rationale_json jsonb,
    risks_json jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

COMMENT ON TABLE haetdeul.proposals IS '매입 Agent T1 시나리오 결과. 실제 Agent 실행 전에는 0행이 정상이다.';

COMMENT ON COLUMN haetdeul.proposals.proposal_id IS '매입 Agent 제안 ID.';

COMMENT ON COLUMN haetdeul.proposals.scenario_id IS '매입 Agent 시나리오 ID.';

COMMENT ON COLUMN haetdeul.proposals.sim_run_id IS '시뮬레이션 실행 ID.';

COMMENT ON COLUMN haetdeul.proposals.purchase_agent_run_id IS 'proposals 테이블의 purchase_agent_run_id 값.';

COMMENT ON COLUMN haetdeul.proposals.as_of IS '판단 시점 기준일.';

COMMENT ON COLUMN haetdeul.proposals.item_id IS '품목 고유 ID.';

COMMENT ON COLUMN haetdeul.proposals.label IS '표시명.';

COMMENT ON COLUMN haetdeul.proposals.total_quantity_kg IS '총수량(kg).';

COMMENT ON COLUMN haetdeul.proposals.max_price_krw_per_kg IS '허용 최대단가(원/kg).';

COMMENT ON COLUMN haetdeul.proposals.timing IS '매입/입고 시점 요약.';

COMMENT ON COLUMN haetdeul.proposals.split_plan_json IS '분할 매입/입고 일정과 회차별 수량 계획 JSON.';

COMMENT ON COLUMN haetdeul.proposals.sourcing_plan_json IS '시장×등급×수량×단가 조달계획 JSON.';

COMMENT ON COLUMN haetdeul.proposals.expected_margin_rate IS '기대마진율.';

COMMENT ON COLUMN haetdeul.proposals.expected_cost_krw IS '매입 Agent 예상원가. 재무 Agent는 실제 Line 기준으로 재계산해야 한다.';

COMMENT ON COLUMN haetdeul.proposals.confidence IS '신뢰도/불확실성 정보.';

COMMENT ON COLUMN haetdeul.proposals.situation IS '판단 대상 상황 설명.';

COMMENT ON COLUMN haetdeul.proposals.status IS '상태값.';

COMMENT ON COLUMN haetdeul.proposals.rationale_json IS '구조화된 판단근거 JSON.';

COMMENT ON COLUMN haetdeul.proposals.risks_json IS '식별된 위험요인 JSON.';

COMMENT ON COLUMN haetdeul.proposals.created_at IS '생성시각.';

ALTER TABLE ONLY haetdeul.proposals
    ADD CONSTRAINT proposals_pkey PRIMARY KEY (proposal_id, scenario_id);

ALTER TABLE ONLY haetdeul.proposals
    ADD CONSTRAINT fk_proposals_agent_run FOREIGN KEY (purchase_agent_run_id) REFERENCES haetdeul.agent_runs(agent_run_id);

ALTER TABLE ONLY haetdeul.proposals
    ADD CONSTRAINT fk_proposals_sim_run FOREIGN KEY (sim_run_id) REFERENCES haetdeul.sim_runs(sim_run_id);

ALTER TABLE ONLY haetdeul.proposals
    ADD CONSTRAINT proposals_item_id_fkey FOREIGN KEY (item_id) REFERENCES haetdeul.items(item_id);

COMMIT;
