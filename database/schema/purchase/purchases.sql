-- purchases — 매입
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/10_domain_schema.sql` (2026-08-30 test DB 에서 뜬 pg_dump 스냅샷)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.purchases (
    purchase_id text NOT NULL,
    sim_run_id text NOT NULL,
    supplier_partner_id text,
    purchase_date date NOT NULL,
    payment_due_date date NOT NULL,
    purchase_type text NOT NULL,
    source_market_label text,
    total_amount_krw numeric(18,6) NOT NULL,
    settlement_status text NOT NULL,
    proposal_id text,
    scenario_id text,
    source_event_id text,
    evidence_id text,
    note text,
    CONSTRAINT purchases_settlement_status_check CHECK ((settlement_status = ANY (ARRAY['SETTLED'::text, 'OPEN'::text, 'CANCELLED'::text]))),
    CONSTRAINT purchases_total_amount_krw_check CHECK ((total_amount_krw >= (0)::numeric))
);

COMMENT ON TABLE haetdeul.purchases IS '매입 Header. 날짜·총액·정산상태·Agent 제안 연결정보를 저장한다.';

COMMENT ON COLUMN haetdeul.purchases.purchase_id IS '매입 Header ID.';

COMMENT ON COLUMN haetdeul.purchases.sim_run_id IS '시뮬레이션 실행 ID.';

COMMENT ON COLUMN haetdeul.purchases.supplier_partner_id IS '매입 공급처 Partner ID.';

COMMENT ON COLUMN haetdeul.purchases.purchase_date IS '매입일.';

COMMENT ON COLUMN haetdeul.purchases.payment_due_date IS '매입대금 지급예정일.';

COMMENT ON COLUMN haetdeul.purchases.purchase_type IS '매입 유형.';

COMMENT ON COLUMN haetdeul.purchases.source_market_label IS '매입가격 산정에 사용한 시장 기준 설명.';

COMMENT ON COLUMN haetdeul.purchases.total_amount_krw IS '매입 Header 총액. purchase_items.line_amount_krw 합계와 일치해야 한다.';

COMMENT ON COLUMN haetdeul.purchases.settlement_status IS '매입 정산상태.';

COMMENT ON COLUMN haetdeul.purchases.proposal_id IS '매입 Agent 제안 ID.';

COMMENT ON COLUMN haetdeul.purchases.scenario_id IS '매입 Agent 시나리오 ID.';

COMMENT ON COLUMN haetdeul.purchases.source_event_id IS '원본/Burn-in 이벤트 ID.';

COMMENT ON COLUMN haetdeul.purchases.evidence_id IS '연결된 근거 원장 ID.';

COMMENT ON COLUMN haetdeul.purchases.note IS '추가 설명 및 주의사항.';

ALTER TABLE ONLY haetdeul.purchases
    ADD CONSTRAINT purchases_pkey PRIMARY KEY (purchase_id);

ALTER TABLE ONLY haetdeul.purchases
    ADD CONSTRAINT fk_purchases_sim_run FOREIGN KEY (sim_run_id) REFERENCES haetdeul.sim_runs(sim_run_id);

ALTER TABLE ONLY haetdeul.purchases
    ADD CONSTRAINT purchases_evidence_id_fkey FOREIGN KEY (evidence_id) REFERENCES haetdeul.evidences(evidence_id);

ALTER TABLE ONLY haetdeul.purchases
    ADD CONSTRAINT purchases_supplier_partner_id_fkey FOREIGN KEY (supplier_partner_id) REFERENCES haetdeul.partners(partner_id);

COMMIT;
