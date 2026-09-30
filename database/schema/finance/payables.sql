-- payables — 재무
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/10_domain_schema.sql` (2026-08-30 test DB 에서 뜬 pg_dump 스냅샷)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.payables (
    payable_id text NOT NULL,
    sim_run_id text NOT NULL,
    purchase_id text NOT NULL,
    issued_date date NOT NULL,
    due_date date NOT NULL,
    original_amount_krw numeric(18,6) NOT NULL,
    paid_amount_krw numeric(18,6) DEFAULT 0 NOT NULL,
    cancelled_amount_krw numeric(18,6) DEFAULT 0 NOT NULL,
    outstanding_amount_krw numeric(18,6) NOT NULL,
    status text NOT NULL,
    settled_date date,
    cancelled_date date,
    CONSTRAINT payables_cancelled_amount_nonnegative_check CHECK ((cancelled_amount_krw >= (0)::numeric)),
    CONSTRAINT payables_cancelled_state_check CHECK (((status <> 'CANCELLED'::text) OR ((outstanding_amount_krw = (0)::numeric) AND (cancelled_date IS NOT NULL)))),
    CONSTRAINT payables_check CHECK ((abs((((original_amount_krw - paid_amount_krw) - cancelled_amount_krw) - outstanding_amount_krw)) < 0.1)),
    CONSTRAINT payables_status_check CHECK ((status = ANY (ARRAY['OPEN'::text, 'PARTIAL'::text, 'SETTLED'::text, 'WRITEOFF'::text, 'CANCELLED'::text])))
);

COMMENT ON TABLE haetdeul.payables IS '매입 건별 매입채무/미지급금 원장.';

COMMENT ON COLUMN haetdeul.payables.payable_id IS '매입채무 ID.';

COMMENT ON COLUMN haetdeul.payables.sim_run_id IS '시뮬레이션 실행 ID.';

COMMENT ON COLUMN haetdeul.payables.purchase_id IS '매입 Header ID.';

COMMENT ON COLUMN haetdeul.payables.issued_date IS '채권/채무 발생일.';

COMMENT ON COLUMN haetdeul.payables.due_date IS '회수/지급 예정일.';

COMMENT ON COLUMN haetdeul.payables.original_amount_krw IS '최초 발생금액(원).';

COMMENT ON COLUMN haetdeul.payables.paid_amount_krw IS '현재까지 지급금액(원).';

COMMENT ON COLUMN haetdeul.payables.cancelled_amount_krw IS '승인/매입 원인 철회로 지급 없이 소멸한 금액(원).';

COMMENT ON COLUMN haetdeul.payables.outstanding_amount_krw IS '현재 미회수/미지급 잔액(원).';

COMMENT ON COLUMN haetdeul.payables.status IS '상태값.';

COMMENT ON COLUMN haetdeul.payables.settled_date IS '완전 정산일.';

COMMENT ON COLUMN haetdeul.payables.cancelled_date IS '미지급 채무가 승인/매입 원인 철회로 취소된 날짜.';

ALTER TABLE ONLY haetdeul.payables
    ADD CONSTRAINT payables_pkey PRIMARY KEY (payable_id);

ALTER TABLE ONLY haetdeul.payables
    ADD CONSTRAINT payables_purchase_id_key UNIQUE (purchase_id);

ALTER TABLE ONLY haetdeul.payables
    ADD CONSTRAINT fk_payables_sim_run FOREIGN KEY (sim_run_id) REFERENCES haetdeul.sim_runs(sim_run_id);

ALTER TABLE ONLY haetdeul.payables
    ADD CONSTRAINT payables_purchase_id_fkey FOREIGN KEY (purchase_id) REFERENCES haetdeul.purchases(purchase_id);

COMMIT;
