-- v_current_finance_state — 재무
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/10_domain_schema.sql` (2026-08-30 test DB 에서 뜬 pg_dump 스냅샷)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.
-- 2026-09-30 BL-021 보완: 새 DB 목록 끝에서 따로 돌던 이관판의 최종 효과를 이 파일에 담았다 —
--   `migrations/finance/finance_current_state_view.sql` 의 뷰 본문과 뷰 주석. 스냅샷 판은
--   `finance_state_id = 'FIN-DAY30-LOAN'` 한 행에 묶여 있었다. 축을 `sim_runs` 에서 잇는 이유와
--   같은 날짜의 두 행을 하나로 줄이지 않는 이유는 그 이관판 머리말에 있다.
--   이미 쓰는 DB 는 그 이관판을 그대로 쓴다(`database/README.md` §2).

BEGIN;

CREATE VIEW haetdeul.v_current_finance_state AS
SELECT
    fs.finance_state_id,
    fs.sim_run_id,
    fs.state_date,
    fs.state_type,
    fs.financing_mode,
    fs.current_cash_krw,
    fs.minimum_operating_cash_krw,
    fs.committed_outflows_krw,
    fs.unsettled_purchase_payables_krw,
    fs.receivables_krw,
    fs.inventory_book_value_krw,
    fs.operational_inventory_value_krw,
    fs.current_debt_krw,
    fs.recommended_loan_amount_krw,
    fs.financial_limit_krw,
    fs.note
FROM haetdeul.finance_states AS fs
JOIN haetdeul.sim_runs AS sr
  ON sr.sim_run_id = fs.sim_run_id
 AND sr.financing_mode = fs.financing_mode
WHERE fs.state_date = (
    SELECT max(latest.state_date)
    FROM haetdeul.finance_states AS latest
    WHERE latest.sim_run_id = fs.sim_run_id
      AND latest.financing_mode = fs.financing_mode
);

COMMENT ON VIEW haetdeul.v_current_finance_state IS
    'sim_runs 가 정한 축(sim_run_id · financing_mode)에서 가장 늦은 재무 상태. '
    '특정 finance_state_id 에 매이지 않는다 — 새 상태가 들어오면 그것이 현재가 된다. '
    '과거 시점 조회는 이 View 가 아니라 as-of 질의가 한다.';

COMMIT;
