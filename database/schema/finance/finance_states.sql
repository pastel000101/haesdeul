-- finance_states — 재무
--
-- 적용 순서는 `database/new_database_order.txt`.
-- 이 파일은 `migrations/finance/finance_state_as_of_index.sql` 의 최종 효과인 as-of 조회 인덱스
--   `ix_finance_states_as_of` 를 담는다. 이미 쓰는 DB 는 그 이관판을 쓴다(`database/README.md` §2).

BEGIN;

CREATE TABLE haetdeul.finance_states (
    finance_state_id text NOT NULL,
    sim_run_id text NOT NULL,
    state_date date NOT NULL,
    state_type text NOT NULL,
    financing_mode text NOT NULL,
    current_cash_krw numeric(18,6) NOT NULL,
    minimum_operating_cash_krw numeric(18,6) NOT NULL,
    committed_outflows_krw numeric(18,6) DEFAULT 0 NOT NULL,
    unsettled_purchase_payables_krw numeric(18,6) DEFAULT 0 NOT NULL,
    receivables_krw numeric(18,6) DEFAULT 0 NOT NULL,
    inventory_book_value_krw numeric(18,6) DEFAULT 0 NOT NULL,
    operational_inventory_value_krw numeric(18,6) DEFAULT 0 NOT NULL,
    current_debt_krw numeric(18,6) DEFAULT 0 NOT NULL,
    recommended_loan_amount_krw numeric(18,6),
    financial_limit_krw numeric(18,6) GENERATED ALWAYS AS ((((current_cash_krw - minimum_operating_cash_krw) - committed_outflows_krw) - unsettled_purchase_payables_krw)) STORED,
    note text
);

COMMENT ON TABLE haetdeul.finance_states IS '특정 시점의 재무 Snapshot. 현재현금·최소운영현금·미수·부채·재고가치·financial_limit을 관리한다.';

COMMENT ON COLUMN haetdeul.finance_states.finance_state_id IS '재무 상태 Snapshot ID.';

COMMENT ON COLUMN haetdeul.finance_states.sim_run_id IS '시뮬레이션 실행 ID.';

COMMENT ON COLUMN haetdeul.finance_states.state_date IS '재무상태 기준일.';

COMMENT ON COLUMN haetdeul.finance_states.state_type IS 'DAY0/DAY30 등 상태유형.';

COMMENT ON COLUMN haetdeul.finance_states.financing_mode IS '자금조달 Scenario.';

COMMENT ON COLUMN haetdeul.finance_states.current_cash_krw IS '현재 현금(원).';

COMMENT ON COLUMN haetdeul.finance_states.minimum_operating_cash_krw IS '매입과 별도로 남겨야 하는 최소 운영현금.';

COMMENT ON COLUMN haetdeul.finance_states.committed_outflows_krw IS '확정 예정 현금유출(원).';

COMMENT ON COLUMN haetdeul.finance_states.unsettled_purchase_payables_krw IS '미정산 매입대금 총액(원).';

COMMENT ON COLUMN haetdeul.finance_states.receivables_krw IS '미수금 총액(원).';

COMMENT ON COLUMN haetdeul.finance_states.inventory_book_value_krw IS '통합 Persona 원본 마감에서 사용한 회계적 재고원가.';

COMMENT ON COLUMN haetdeul.finance_states.operational_inventory_value_krw IS '현재 inventory_lots 잔량×Lot 원가 기준 운영 재고가치.';

COMMENT ON COLUMN haetdeul.finance_states.current_debt_krw IS '현재 부채/대출잔액(원).';

COMMENT ON COLUMN haetdeul.finance_states.recommended_loan_amount_krw IS 'Persona 검토에서 산출한 권장 대출액. 실제 승인 대출액이 아님.';

COMMENT ON COLUMN haetdeul.finance_states.financial_limit_krw IS '재무 Agent 최대 집행가능금액 기준. current_cash-minimum_operating_cash-committed_outflows-unsettled_purchase_payables 자동계산.';

COMMENT ON COLUMN haetdeul.finance_states.note IS '추가 설명 및 주의사항.';

ALTER TABLE ONLY haetdeul.finance_states
    ADD CONSTRAINT finance_states_pkey PRIMARY KEY (finance_state_id);

ALTER TABLE ONLY haetdeul.finance_states
    ADD CONSTRAINT uq_finance_states_axis_date UNIQUE (sim_run_id, financing_mode, state_date);

-- 같은 sim_run · 같은 financing_mode 안에서 state_date <= as_of 중 가장 늦은 행을 고르는 조회와
-- `v_current_finance_state` 의 축별 max(state_date) 를 받는다.
CREATE INDEX IF NOT EXISTS ix_finance_states_as_of
    ON haetdeul.finance_states (sim_run_id, financing_mode, state_date DESC);

ALTER TABLE ONLY haetdeul.finance_states
    ADD CONSTRAINT fk_finance_states_sim_run FOREIGN KEY (sim_run_id) REFERENCES haetdeul.sim_runs(sim_run_id);

COMMIT;
