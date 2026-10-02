-- expenses — 재무
--
-- 적용 순서는 `database/new_database_order.txt`.
-- 이 파일은 `migrations/finance/expense_lifecycle.sql` 의 최종 효과인 인덱스 둘
--   (`expenses_projection_axis_idx` · `expenses_paid_axis_idx`)을 담는다. 그 이관판의 칸
--   (`due_date` · `paid_date` · 마감의 `operating_expense_cash_out_krw`)과 칸 주석도 이 파일에
--   같은 모양으로 있다. 이미 쓰는 DB 는 그 이관판을 쓴다(`database/README.md` §2).

BEGIN;

CREATE TABLE haetdeul.expenses (
    expense_id text NOT NULL,
    sim_run_id text NOT NULL,
    expense_date date NOT NULL,
    expense_category text NOT NULL,
    amount_krw numeric(18,6) NOT NULL,
    is_fixed boolean NOT NULL,
    related_delivery_id text,
    evidence_id text,
    status text NOT NULL,
    note text,
    due_date date,
    paid_date date,
    source_ref text,
    recorded_by text,
    CONSTRAINT expenses_amount_krw_check CHECK ((amount_krw >= (0)::numeric)),
    CONSTRAINT expenses_status_check CHECK ((status = ANY (ARRAY['PAID'::text, 'ACCRUED'::text, 'CANCELLED'::text])))
);

COMMENT ON TABLE haetdeul.expenses IS '매입대금 이외 인건비·물류비·이자 등 운영비 원장.';

COMMENT ON COLUMN haetdeul.expenses.expense_id IS '운영비 ID.';

COMMENT ON COLUMN haetdeul.expenses.sim_run_id IS '시뮬레이션 실행 ID.';

COMMENT ON COLUMN haetdeul.expenses.expense_date IS '비용 발생일.';

COMMENT ON COLUMN haetdeul.expenses.expense_category IS '비용 분류.';

COMMENT ON COLUMN haetdeul.expenses.amount_krw IS '금액(원).';

COMMENT ON COLUMN haetdeul.expenses.is_fixed IS '고정비 여부.';

COMMENT ON COLUMN haetdeul.expenses.related_delivery_id IS '연결된 배송 ID.';

COMMENT ON COLUMN haetdeul.expenses.evidence_id IS '연결된 근거 원장 ID.';

COMMENT ON COLUMN haetdeul.expenses.status IS '상태값.';

COMMENT ON COLUMN haetdeul.expenses.note IS '추가 설명 및 주의사항.';

COMMENT ON COLUMN haetdeul.expenses.due_date IS '지급 예정일. ACCRUED 비용의 미래 현금유출 투영 기준일이다. 신규 비용은 반드시 채운다.';

COMMENT ON COLUMN haetdeul.expenses.paid_date IS '실제 지급일. PAID 비용의 현금 차감·일마감 기준일이다. 값이 없는 기존 PAID 행은 지급일 미상이다.';

COMMENT ON COLUMN haetdeul.expenses.source_ref IS '앞선 작업이 남긴 칸. 비용 생명주기의 쓰기 계약이 아니다 — 비용 근거의 정본은 evidence_id 하나다.';

COMMENT ON COLUMN haetdeul.expenses.recorded_by IS '앞선 작업이 남긴 칸. 비용 생명주기의 쓰기 계약이 아니다.';

ALTER TABLE ONLY haetdeul.expenses
    ADD CONSTRAINT expenses_pkey PRIMARY KEY (expense_id);

-- 미래 현금유출 투영은 «그 실행의 ACCRUED 를 지급 예정일 순서로» 읽는다.
CREATE INDEX IF NOT EXISTS expenses_projection_axis_idx
    ON haetdeul.expenses (sim_run_id, status, due_date);

-- 일마감은 «그 실행의 PAID 를 지급일로» 읽는다.
CREATE INDEX IF NOT EXISTS expenses_paid_axis_idx
    ON haetdeul.expenses (sim_run_id, status, paid_date);

ALTER TABLE ONLY haetdeul.expenses
    ADD CONSTRAINT expenses_evidence_id_fkey FOREIGN KEY (evidence_id) REFERENCES haetdeul.evidences(evidence_id);

ALTER TABLE ONLY haetdeul.expenses
    ADD CONSTRAINT expenses_related_delivery_id_fkey FOREIGN KEY (related_delivery_id) REFERENCES haetdeul.deliveries(delivery_id);

ALTER TABLE ONLY haetdeul.expenses
    ADD CONSTRAINT fk_expenses_sim_run FOREIGN KEY (sim_run_id) REFERENCES haetdeul.sim_runs(sim_run_id);

COMMIT;
