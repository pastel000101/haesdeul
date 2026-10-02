-- 출고 준비일 정책 Seed — outbound_prep_lead_days (물류 · WP-4 M4)
--
-- ══════════════════════════════════════════════════════════════════════════
-- 주의: 이 파일은 아직 실 DB 에 적용하지 않았다. 적용 시점은 따로 정한다.
-- ══════════════════════════════════════════════════════════════════════════
--
-- 왜 만드는가 — 값은 이미 있는데 활성 판에 없다.
--
--   실측(2026-09-09 · `agent_policy_config`):
--
--     domain     policy_key                policy_version     value  is_active
--     logistics  outbound_prep_lead_days   v0.5.2-PERSONA     1      FALSE     ← 있다
--     logistics  outbound_prep_lead_days   v1.3-PROVISIONAL   —      —         ← 없다
--
--   물류가 실제로 읽는 판은 `v1.3-PROVISIONAL` 이다
--   (`app/logistics/schemas/snapshot.py` 의 `LOGISTICS_POLICY_VERSION = POLICY_VERSION`).
--   그래서 PRE_SALES 는 "가장 이른 납기일" 을 낼 근거가 없다 — 값을 새로 정하는 것이
--   아니라 이미 결정된 값을 활성 판에 싣는다.
--
-- `source_ref` 를 발명하지 않는다. `v0.5.2-PERSONA` 행이 들고 있는
--    `PERSONA-V0.5.2:06-§2.1,§11` 을 그대로 쓴다. 그것이 이 값의 실제 출처이고,
--    다른 정책 8행도 판이 바뀔 때 `source_ref` 를 그대로 이어 썼다 (실측 —
--    `guaranteed_capacity_kg` 만 판마다 다르고 나머지는 두 판이 같은 문자열이다).
--
--    `MVP-DECISION-20260909:...` 같은 새 식별자를 만들지 않는다. 저장소·실 DB
--       어디에도 2026-09-0x 결정 식별자가 없어(실측 0건) 새로 만들면 가리키는
--       문서가 없는 근거가 된다. 없는 문서를 만드는 것보다 있는 출처를 잇는다.
--
-- 값 규칙: 정수 · `>= 0` · 확정값 1 calendar day.
--    `app/logistics/domain/snapshot.py` 의 `_INTEGER_POLICY_KEYS` 가 소수점을 거부한다.
--
-- `transport_lead_time` 은 정책 행으로 안 만든다. MVP 확정값이 0 일이고 그것은
--    "운송 소요시간의 정본이 스키마에 없다" 는 사실의 표현이지 운영 정책값이 아니다
--    (`TransportPlan.standard_minutes` 가 늘 `None` 인 그 자리).
--    정책 행으로 만들면 없는 정본이 생긴 것처럼 보인다 —
--    `app/logistics/domain/tools.py` 의 `TRANSPORT_LEAD_DAYS` 가 그 이유와 함께 상수로 들고 있다.
--
-- 선택 정책 자리에 넣는다. `app/logistics/domain/snapshot.py` 의
--    `_OPTIONAL_NUMERIC_POLICY_KEYS` 다 —
--    필수로 올리면 이 행이 없는 DB 에서 입고·Capacity 처럼 납기와 무관한 경로까지
--    통째로 `RUNTIME_NOT_READY` 가 된다. 없을 때의 fail-closed 는 이 값을 실제로
--    쓰는 자리(PRE_SALES 납기 판정)가 건다.
--
-- NOT NULL 칸: `evidence_grade='SIM_FIXED'` · `approved_by='HUMAN'` —
--   기존 물류 정책 행의 관례 그대로다 (`logistics_llm_policy_seed.sql` 머리말).
--
-- 멱등: 두 번 돌려도 안전하다. 가드는 `uq_agent_policy_version`
--   `(policy_version, domain, policy_key)` 과 같은 열로 건다 — `usage_scope` 를
--   더 걸면 scope 만 다른 기존 행이 있을 때 가드를 통과하고 유니크 위반으로 죽는다
--   (기존 Seed 가 적어 둔 그 함정이다).

INSERT INTO haetdeul.agent_policy_config (
    domain, policy_key, value_kind, value_numeric, value_text, value_json,
    evidence_grade, approved_by,
    source_ref, policy_version, usage_scope, is_active
)
SELECT 'logistics', 'outbound_prep_lead_days', 'NUMERIC', 1, NULL, NULL,
       'SIM_FIXED', 'HUMAN',
       'PERSONA-V0.5.2:06-§2.1,§11', 'v1.3-PROVISIONAL', 'AGENT_MVP_DEMO', TRUE
WHERE NOT EXISTS (
    SELECT 1 FROM haetdeul.agent_policy_config
    WHERE domain = 'logistics'
      AND policy_key = 'outbound_prep_lead_days'
      AND policy_version = 'v1.3-PROVISIONAL'
);

-- ═══════════════════════════════════════════════════════════════════════════
-- 적용 뒤 검증
-- ═══════════════════════════════════════════════════════════════════════════
--
--   ① 활성 판에 1행이 섰나 — value_numeric = 1 · is_active = TRUE
--     SELECT policy_key, value_numeric, source_ref, policy_version, is_active
--       FROM haetdeul.agent_policy_config
--      WHERE domain = 'logistics' AND policy_key = 'outbound_prep_lead_days'
--      ORDER BY policy_version;
--
--   ② 옛 판이 안 바뀌었나 — v0.5.2-PERSONA 행은 is_active = FALSE 그대로
--
--   ③ Loader 가 읽나 (production 함수)
--     app/logistics/readmodel/current.py 의
--     get_active_logistics_policy(conn).outbound_prep_lead_days == 1
--
--   ④ 이 Seed 를 넣기 전 값은 None 이어야 한다 — 코드 기본값 1 이 들어가면 이 Seed 가 무의미하다
