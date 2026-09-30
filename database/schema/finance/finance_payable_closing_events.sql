-- finance_payable_closing_events — 재무
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/10_domain_schema.sql` (2026-08-30 test DB 에서 뜬 pg_dump 스냅샷)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.finance_payable_closing_events (
    sim_run_id text NOT NULL,
    payable_id text NOT NULL,
    recognized_date date NOT NULL,
    recognized_amount_krw numeric(18,6) NOT NULL,
    due_date date NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT finance_payable_closing_events_amount_check CHECK ((recognized_amount_krw >= (0)::numeric))
);

COMMENT ON TABLE haetdeul.finance_payable_closing_events IS '매입대금이 일마감 현금곡선에 실린 사실. 한 실행에서 한 payable 은 한 번이다. 실제 지급(paid_amount_krw·status)과 다른 축이고, 여기는 현금곡선 귀속만 적는다.';

COMMENT ON COLUMN haetdeul.finance_payable_closing_events.sim_run_id IS '어느 실행의 장부인가. 이 칸이 있어야 sim-run reset 이 이 표를 자동으로 비운다.';

COMMENT ON COLUMN haetdeul.finance_payable_closing_events.recognized_date IS '🔴 현금곡선에 실은 날이다. 계약 기일이 아니다 — payable 이 늦게 생기면 due_date 보다 뒤다.';

COMMENT ON COLUMN haetdeul.finance_payable_closing_events.recognized_amount_krw IS '실은 금액. 마감 시점의 outstanding_amount_krw 다. original_amount_krw 를 쓰면 PARTIAL 에서 이미 나간 몫까지 다시 센다.';

COMMENT ON COLUMN haetdeul.finance_payable_closing_events.due_date IS '계약 기일. 원장 그대로다. recognized_date 와 나란히 봐야 왜 이 날 실렸는지 되짚을 수 있다.';

ALTER TABLE ONLY haetdeul.finance_payable_closing_events
    ADD CONSTRAINT finance_payable_closing_events_pkey PRIMARY KEY (sim_run_id, payable_id);

CREATE INDEX finance_payable_closing_events_run_date_idx ON haetdeul.finance_payable_closing_events USING btree (sim_run_id, recognized_date);

ALTER TABLE ONLY haetdeul.finance_payable_closing_events
    ADD CONSTRAINT finance_payable_closing_events_payable_fkey FOREIGN KEY (payable_id) REFERENCES haetdeul.payables(payable_id);

COMMIT;
