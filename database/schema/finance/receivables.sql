-- receivables — 재무
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/10_domain_schema.sql` (2026-08-30 test DB 에서 뜬 pg_dump 스냅샷)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.receivables (
    receivable_id text NOT NULL,
    sim_run_id text NOT NULL,
    sale_id text NOT NULL,
    issued_date date NOT NULL,
    due_date date NOT NULL,
    original_amount_krw numeric(18,6) NOT NULL,
    received_amount_krw numeric(18,6) DEFAULT 0 NOT NULL,
    outstanding_amount_krw numeric(18,6) NOT NULL,
    status text NOT NULL,
    CONSTRAINT receivables_check CHECK ((abs(((original_amount_krw - received_amount_krw) - outstanding_amount_krw)) < 0.1)),
    CONSTRAINT receivables_status_check CHECK ((status = ANY (ARRAY['OPEN'::text, 'PARTIAL'::text, 'COLLECTED'::text, 'WRITEOFF'::text])))
);

COMMENT ON TABLE haetdeul.receivables IS '판매 건별 매출채권/미수금 원장.';

COMMENT ON COLUMN haetdeul.receivables.receivable_id IS '매출채권 ID.';

COMMENT ON COLUMN haetdeul.receivables.sim_run_id IS '시뮬레이션 실행 ID.';

COMMENT ON COLUMN haetdeul.receivables.sale_id IS '판매 Header ID.';

COMMENT ON COLUMN haetdeul.receivables.issued_date IS '채권/채무 발생일.';

COMMENT ON COLUMN haetdeul.receivables.due_date IS '회수/지급 예정일.';

COMMENT ON COLUMN haetdeul.receivables.original_amount_krw IS '최초 발생금액(원).';

COMMENT ON COLUMN haetdeul.receivables.received_amount_krw IS '현재까지 회수금액(원).';

COMMENT ON COLUMN haetdeul.receivables.outstanding_amount_krw IS '현재 미회수/미지급 잔액(원).';

COMMENT ON COLUMN haetdeul.receivables.status IS '상태값.';

ALTER TABLE ONLY haetdeul.receivables
    ADD CONSTRAINT receivables_pkey PRIMARY KEY (receivable_id);

ALTER TABLE ONLY haetdeul.receivables
    ADD CONSTRAINT receivables_sale_id_key UNIQUE (sale_id);

ALTER TABLE ONLY haetdeul.receivables
    ADD CONSTRAINT fk_receivables_sim_run FOREIGN KEY (sim_run_id) REFERENCES haetdeul.sim_runs(sim_run_id);

ALTER TABLE ONLY haetdeul.receivables
    ADD CONSTRAINT receivables_sale_id_fkey FOREIGN KEY (sale_id) REFERENCES haetdeul.sales(sale_id);

COMMIT;
