-- daily_closings — 재무
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/10_domain_schema.sql` (2026-08-30 test DB 에서 뜬 pg_dump 스냅샷)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.daily_closings (
    sim_run_id text NOT NULL,
    close_date date NOT NULL,
    day_no integer NOT NULL,
    purchase_cash_out_krw numeric(18,6) NOT NULL,
    logistics_cash_out_krw numeric(18,6) NOT NULL,
    payroll_interest_cash_out_krw numeric(18,6) NOT NULL,
    sales_recognized_krw numeric(18,6) NOT NULL,
    collection_cash_in_krw numeric(18,6) NOT NULL,
    base_net_cash_krw numeric(18,6) NOT NULL,
    base_cash_balance_krw numeric(18,6) NOT NULL,
    loan_execution_krw numeric(18,6) NOT NULL,
    loan_cash_balance_krw numeric(18,6) NOT NULL,
    receivables_balance_krw numeric(18,6) NOT NULL,
    inventory_qty_kg numeric(18,6) NOT NULL,
    accounting_inventory_cost_krw numeric(18,6) NOT NULL,
    closed boolean DEFAULT true NOT NULL,
    operating_expense_cash_out_krw numeric(18,6)
);

COMMENT ON TABLE haetdeul.daily_closings IS '매입·판매·물류·현금·미수·재고를 일자별로 집계한 마감 결과.';

COMMENT ON COLUMN haetdeul.daily_closings.sim_run_id IS '시뮬레이션 실행 ID.';

COMMENT ON COLUMN haetdeul.daily_closings.close_date IS '일별 마감 기준일.';

COMMENT ON COLUMN haetdeul.daily_closings.day_no IS '시뮬레이션/Burn-in Day 순번.';

COMMENT ON COLUMN haetdeul.daily_closings.purchase_cash_out_krw IS '일별 매입 현금유출(원).';

COMMENT ON COLUMN haetdeul.daily_closings.logistics_cash_out_krw IS '일별 물류 현금유출(원).';

COMMENT ON COLUMN haetdeul.daily_closings.payroll_interest_cash_out_krw IS '일별 급여/이자 현금유출(원).';

COMMENT ON COLUMN haetdeul.daily_closings.operating_expense_cash_out_krw IS '일별 일반 운영비 현금유출(원). NULL 은 그 실행이 이 축을 기록하지 않았다는 뜻이고, 0원과 다르다.';

COMMENT ON COLUMN haetdeul.daily_closings.sales_recognized_krw IS '일별 발생 매출액(원).';

COMMENT ON COLUMN haetdeul.daily_closings.collection_cash_in_krw IS '일별 실제 매출대금 현금유입(원).';

COMMENT ON COLUMN haetdeul.daily_closings.base_net_cash_krw IS '대출 제외 일일 순현금 증감(원).';

COMMENT ON COLUMN haetdeul.daily_closings.base_cash_balance_krw IS '대출 없는 Base 시나리오 현금잔액(원).';

COMMENT ON COLUMN haetdeul.daily_closings.loan_execution_krw IS '해당 일자 대출 실행액(원).';

COMMENT ON COLUMN haetdeul.daily_closings.loan_cash_balance_krw IS '대출 반영 후 현금잔액(원).';

COMMENT ON COLUMN haetdeul.daily_closings.receivables_balance_krw IS '마감 시점 미수금 잔액(원).';

COMMENT ON COLUMN haetdeul.daily_closings.inventory_qty_kg IS '마감 시점 재고수량(kg).';

COMMENT ON COLUMN haetdeul.daily_closings.accounting_inventory_cost_krw IS '마감 기준 회계적 재고원가(원).';

COMMENT ON COLUMN haetdeul.daily_closings.closed IS '일별 마감 완료 여부. 다음 날 Agent 실행 전제 검증에 사용.';

ALTER TABLE ONLY haetdeul.daily_closings
    ADD CONSTRAINT daily_closings_pkey PRIMARY KEY (sim_run_id, close_date);

ALTER TABLE ONLY haetdeul.daily_closings
    ADD CONSTRAINT fk_daily_closings_sim_run FOREIGN KEY (sim_run_id) REFERENCES haetdeul.sim_runs(sim_run_id);

COMMIT;
