-- logistics_action_proposals — 물류
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/40_logistics_agent_schema.sql` (2026-09-12 · 09-13 물류 Agent Core)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

-- ═══════════════════════════════════════════════════════════════════════════
-- §4  대응안 — "그 문제에 무엇을 할까" 한 줄  (#628 Commit 5)
--
--     🔴 **APPROVED 는 EXECUTED 가 아니다.**
--
--     ```text
--     APPROVED   "이 대응안으로 진행해도 좋다" 는 **사람의 결정**
--     ≠          판매가 일어났다 · 매입 일정이 바뀌었다 · 재고가 폐기됐다
--     ```
--
--        승인 뒤에도 `inventory_lots` · `inventory_moves` · `sales` · 매입 일정은
--        한 줄도 안 바뀐다. 실제 실행은 **Commit 6** 이고, 그때까지 이 표가 드는 것은
--        *"사람이 무엇을 승인했나"* 라는 사실 하나뿐이다.
--
--     🔴 **숫자를 여기서 만들지 않는다.** `impact_json` 은 Commit 3 의
--        `estimate_action_impact` 가 낸 답 그대로이고, 그 계산은 기존 결정론
--        계산기가 한다. 제안이 저장되면서 다시 셈하는 값은 하나도 없다.
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS haetdeul.logistics_action_proposals (
    -- PRP-{exception_id}-{n}. 업무 키다 — 어느 문제의 몇 번째 대응안인가.
    proposal_id           TEXT NOT NULL,
    -- 🔴 리셋 축. 이 칸이 있어서 `reset_sim_run_ledger` 가 표를 **자동으로 발견**한다.
    sim_run_id            TEXT NOT NULL,
    exception_id          TEXT NOT NULL,
    -- 🔴 **이 제안을 낸 조사** (§3 · Commit 7). 아래 복합 FK 가 «같은 실행 · 같은
    --    문제의» 조사만 가리키게 하고, 부분 유일 인덱스가 **한 조사 한 제안**을 지킨다.
    -- ⚠️ **FK 가 못 막는 것이 하나 있다.** 같은 문제를 두 번 조사하면 `INV-A` 와 `INV-B`
    --    가 나란히 서고 둘은 실행도 문제도 같다 — 그래서 «A 의 ID 에 B 의 결과» 를 매다는
    --    호출은 FK 를 통과한다. 그 자리는 응용(`proposal_service._check_investigation`)이
    --    저장된 조사 snapshot 과 넘어온 결과를 **대조해서** 막는다.
    -- ⚠️ **nullable 이다.** 손으로 세운 제안과 Commit 5 시절의 기존 행은 가리킬 조사가
    --    없다 — NOT NULL 로 막으면 그 행들이 통째로 불법이 된다. NULL 이면 복합 FK 도
    --    검사하지 않는다 (MATCH SIMPLE).
    -- 🔴 **지문(`proposal_key`)에는 안 들어간다.** 같은 안을 다시 조사해서 다시 올린
    --    것도 «같은 뜻의 제안» 이다 — 조사 ID 를 섞으면 지문이 늘 달라져 재시도를
    --    아무것도 못 막는다.
    investigation_id      TEXT,
    -- 재시도 식별용 지문 = f(sim_run_id, exception_id, action_type, 정규화된 parameters).
    -- 🔴 **유일 제약이 아니다** — 거절된 뒤 같은 안을 다시 올리는 것은 정상이다.
    proposal_key          TEXT NOT NULL,

    action_type           TEXT NOT NULL,
    -- 🔴 **모델이 고른 값이 아니다.** `ACTION_DECISION_OWNERS` 에서 다시 계산한다.
    --    ⚠️ `approved_by`(승인한 사람) 와 다른 축이다 — 승인은 사람이 하고, 그 뒤
    --       실제 실행 책임은 이 부서가 진다.
    decision_owner        TEXT NOT NULL,
    parameters_json       JSONB NOT NULL,
    -- `estimate_action_impact` 의 답 그대로. feasibility ∈ FEASIBLE·UNRESOLVED.
    -- 🔴 `UNRESOLVED` 를 숫자로 메워 `FEASIBLE` 로 바꾸지 않는다 (상세설계 §27).
    impact_json           JSONB NOT NULL,
    -- [{sequence, tool_name, arguments, facts, observed_as_of, uncertainties}, …]
    -- 🔴 번호만 적지 않는다 — 조사 실행이 저장되지 않으므로 번호는 가리킬 곳이 없다.
    -- 🔴 **Tool 답 전체도 적지 않는다** — 제안은 조사 로그 저장소가 아니다.
    --    `facts` 는 승인 판단에 실제로 쓴 칸만 담는다 (상세설계 §10.4 ⑧).
    -- ⚠️ **실행 결과를 여기 섞지 않는다.** 이 칸은 *"왜 승인했나"* 이고, *"무엇이
    --    실행됐나"* 는 `execution_result_json` 이다.
    evidence_refs_json    JSONB NOT NULL,
    -- 모델이 적은 문장. **업무 사실이 아니라 기록이다** — 숫자의 주인은 impact_json.
    rationale             TEXT NOT NULL DEFAULT '',
    -- 이 제안을 낸 조사가 어떻게 끝났나 (FINISHED · BUDGET_EXCEEDED · …) / AI 가 실제로
    -- 판단했나 (SUCCESS · FALLBACK · DISABLED · SKIPPED_TEMPLATE).
    source_finish_reason  TEXT,
    source_llm_status     TEXT,

    status                TEXT NOT NULL,

    -- 시뮬레이션 영업일. 🔴 `created_at`(벽시계)과 다른 축이다.
    proposed_as_of        DATE NOT NULL,
    approved_as_of        DATE,
    rejected_as_of        DATE,
    expired_as_of         DATE,
    superseded_as_of      DATE,
    -- 🔴 **승인은 끝이 아니다** (Commit 6). `approved_as_of` 와 `executed_as_of` 가 한
    --    행에 함께 서는 것이 **정상 흐름**이다 — D5 제안 · D6 승인 · D8 실행.
    executed_as_of        DATE,
    failed_as_of          DATE,
    -- 🔴 조사 결과의 값을 **그대로** 옮긴다. 제안일·승인일·현재시각으로 메우지 않는다.
    observed_as_of        DATE,

    proposed_by           TEXT NOT NULL,
    approved_by           TEXT,
    rejected_by           TEXT,
    approval_note         TEXT,
    rejection_reason      TEXT,
    -- ⚠️ `decision_owner`(실행 책임 부서) 와 **다른 축이다.** 실제로 돌린 주체다.
    --    예: decision_owner=LOGISTICS · executed_by=master-runner.
    executed_by           TEXT,
    -- 무엇을 실행했고 어느 정본 행을 가리키나. 🔴 **업무 표 snapshot 이 아니다** —
    -- 실행 함수가 낸 authoritative 값만 담고, 여기서 숫자를 새로 만들지 않는다.
    execution_result_json JSONB,
    -- 🔴 **확정된 실패만.** «모른다» 를 실패로 적지 않는다 (상세설계 §10.5).
    failure_code          TEXT,
    failure_reason        TEXT,

    -- 이 제안이 대체한 이전 제안.
    previous_proposal_id  TEXT,

    -- 🔴 DB 벽시계. 감사용이고 **영업 판단에 쓰지 않는다.**
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT logistics_action_proposals_pkey PRIMARY KEY (proposal_id),
    -- 🔴 아래 자기참조 복합 FK 가 가리킬 자리. PK 가 이미 유일성을 주지만 복합 FK 는
    --    «유일한 칸 묶음» 만 가리킬 수 있다.
    CONSTRAINT uq_logistics_action_proposals_axis
        UNIQUE (sim_run_id, exception_id, proposal_id),

    CONSTRAINT logistics_action_proposals_sim_run_id_fkey
        FOREIGN KEY (sim_run_id) REFERENCES haetdeul.sim_runs(sim_run_id),
    CONSTRAINT logistics_action_proposals_exception_fkey
        FOREIGN KEY (exception_id)
        REFERENCES haetdeul.logistics_exceptions(exception_id),
    -- 🔴 **실행 축을 DB 가 지킨다.** 위의 홑 FK 만으로는 «RUN-B 의 제안이 RUN-A 의
    --    문제를 가리키는» 조합을 못 막는다 — 두 FK 가 각자 자기 칸만 보기 때문이다.
    --    응용이 이미 막고 있어도 장부의 불변식은 DB 에도 있어야 한다.
    CONSTRAINT logistics_action_proposals_exception_axis_fkey
        FOREIGN KEY (sim_run_id, exception_id)
        REFERENCES haetdeul.logistics_exceptions (sim_run_id, exception_id),
    -- 🔴 **제안은 «같은 실행 · 같은 문제를» 조사한 기록만 가리킨다** (Commit 7).
    --    없는 조사 ID · RUN-B 의 조사 · EX-B 의 조사를 전부 여기서 막는다.
    --    ⚠️ `investigation_id` 가 NULL 이면 검사하지 않는다 (MATCH SIMPLE) — 손으로
    --       세운 제안에는 가리킬 조사가 없다.
    CONSTRAINT logistics_action_proposals_investigation_axis_fkey
        FOREIGN KEY (sim_run_id, exception_id, investigation_id)
        REFERENCES haetdeul.logistics_investigations
                   (sim_run_id, exception_id, investigation_id),
    CONSTRAINT logistics_action_proposals_previous_fkey
        FOREIGN KEY (previous_proposal_id)
        REFERENCES haetdeul.logistics_action_proposals(proposal_id),
    -- 🔴 **대체는 같은 실행 · 같은 문제 안에서만.** `previous_proposal_id` 가 NULL 이면
    --    (MATCH SIMPLE) 검사하지 않는다 — 첫 제안은 가리킬 앞이 없다.
    CONSTRAINT logistics_action_proposals_previous_axis_fkey
        FOREIGN KEY (sim_run_id, exception_id, previous_proposal_id)
        REFERENCES haetdeul.logistics_action_proposals
                   (sim_run_id, exception_id, proposal_id),

    -- 🔴 카탈로그는 닫혀 있다 (상세설계 §10.1). 모델이 이름을 지어내도 행이 안 된다.
    CONSTRAINT ck_logistics_action_proposals_action
        CHECK (action_type IN ('SALES_PRIORITY_REQUEST', 'PURCHASE_ADJUST_REQUEST',
                               'ACCEPT_RISK', 'DISPOSAL_REQUEST')),
    CONSTRAINT ck_logistics_action_proposals_owner
        CHECK (decision_owner IN ('LOGISTICS', 'SALES', 'PURCHASE')),
    -- ⚠️ EXECUTED · FAILED 는 **어휘로만** 둔다 — Commit 5 의 Production 코드는 이
    --    둘로 전이시키지 않는다. 실행은 Commit 6 이다.
    CONSTRAINT ck_logistics_action_proposals_status
        CHECK (status IN ('PROPOSED', 'APPROVED', 'REJECTED', 'EXPIRED', 'SUPERSEDED',
                          'EXECUTED', 'FAILED')),

    -- 🔴 **결정에는 날과 사람이 있다.** 둘 중 하나가 비면 "누가 언제 정했나" 를 못 댄다.
    CONSTRAINT ck_logistics_action_proposals_approved
        CHECK (status <> 'APPROVED'
               OR (approved_as_of IS NOT NULL AND approved_by IS NOT NULL)),
    -- 🔴 **승인 provenance 는 실행 뒤에도 남는다** (Commit 6 보정). 위 제약은 상태가
    --    `APPROVED` 일 때만 보므로, 실행이 상태를 옮긴 순간 «누가 승인했나» 가 비어도
    --    DB 가 통과시켰다 — *"승인은 됐는데 누가 했는지 모른다"* 는 행이 설 수 있었다.
    --    ⚠️ 위 제약을 지우지 않고 **더한다** — 이 파일의 DROP 0 규율 그대로이고, 새
    --       제약이 더 강해 둘이 충돌하지 않는다.
    CONSTRAINT ck_logistics_action_proposals_approval_provenance
        CHECK (status NOT IN ('APPROVED', 'EXECUTED', 'FAILED')
               OR (approved_as_of IS NOT NULL AND approved_by IS NOT NULL)),
    CONSTRAINT ck_logistics_action_proposals_rejected
        CHECK (status <> 'REJECTED'
               OR (rejected_as_of IS NOT NULL AND rejected_by IS NOT NULL
                   AND rejection_reason IS NOT NULL)),
    CONSTRAINT ck_logistics_action_proposals_expired
        CHECK (status <> 'EXPIRED' OR expired_as_of IS NOT NULL),
    CONSTRAINT ck_logistics_action_proposals_superseded
        CHECK (status <> 'SUPERSEDED' OR superseded_as_of IS NOT NULL),

    -- 🔴 **«결정» 의 날은 하나다.** 승인도 거절도 만료도 대체도 한 제안에 한 번뿐이다.
    --    ★ **실행 날짜는 이 셈에 안 들어간다** (Commit 6). `executed_as_of` 는 결정이
    --      아니라 그 결정을 **수행한** 날이라, `approved_as_of` 와 함께 서는 것이 정상
    --      흐름이다 — 그래서 이 제약을 고칠 필요가 없었다(축이 다르다).
    --    ⚠️ APPROVED → SUPERSEDED 를 열어야 한다면 그때는 이 제약과 과거 재현의
    --       우선순위를 **함께** 다시 정해야 한다 — 한쪽만 풀면 조회가 조용히 틀린다.
    CONSTRAINT ck_logistics_action_proposals_single_terminal
        CHECK ((approved_as_of    IS NOT NULL)::int
             + (rejected_as_of    IS NOT NULL)::int
             + (expired_as_of     IS NOT NULL)::int
             + (superseded_as_of  IS NOT NULL)::int <= 1),

    -- ── 실행 축 (Commit 6) ────────────────────────────────────────────
    -- 🔴 **실행은 승인 뒤에만 있다.** 승인 없이 실행된 행은 «누가 진행해도 좋다고
    --    했나» 를 못 댄다.
    CONSTRAINT ck_logistics_action_proposals_executed
        CHECK (status <> 'EXECUTED'
               OR (approved_as_of IS NOT NULL AND executed_as_of IS NOT NULL
                   AND executed_by IS NOT NULL AND failed_as_of IS NULL)),
    CONSTRAINT ck_logistics_action_proposals_failed
        CHECK (status <> 'FAILED'
               OR (approved_as_of IS NOT NULL AND failed_as_of IS NOT NULL
                   AND failure_code IS NOT NULL AND executed_as_of IS NULL)),
    -- 🔴 **실행 날짜를 드는 상태는 둘뿐이다.** `APPROVED` 인데 실행일이 적혀 있으면
    --    그날 상태를 못 고른다.
    CONSTRAINT ck_logistics_action_proposals_execution_owner
        CHECK (status IN ('EXECUTED', 'FAILED')
               OR (executed_as_of IS NULL AND failed_as_of IS NULL
                   AND execution_result_json IS NULL AND failure_code IS NULL)),
    -- 성공과 실패가 한 행에 같이 설 수 없다.
    CONSTRAINT ck_logistics_action_proposals_execution_exclusive
        CHECK (executed_as_of IS NULL OR failed_as_of IS NULL),
    -- 승인보다 앞서 실행될 수 없다.
    CONSTRAINT ck_logistics_action_proposals_execution_order
        CHECK ((executed_as_of IS NULL OR executed_as_of >= approved_as_of)
           AND (failed_as_of   IS NULL OR failed_as_of   >= approved_as_of)),
    -- 🔴 사람·주체 칸을 빈 문자열로 채우지 않는다.
    CONSTRAINT ck_logistics_action_proposals_execution_actor
        CHECK ((executed_by   IS NULL OR length(btrim(executed_by)) > 0)
           AND (failure_code  IS NULL OR length(btrim(failure_code)) > 0)
           AND (execution_result_json IS NULL
                OR jsonb_typeof(execution_result_json) = 'object')),

    -- 제안된 날보다 앞서 결정될 수 없다.
    CONSTRAINT ck_logistics_action_proposals_decided_order
        CHECK (COALESCE(approved_as_of,   proposed_as_of) >= proposed_as_of
           AND COALESCE(rejected_as_of,   proposed_as_of) >= proposed_as_of
           AND COALESCE(expired_as_of,    proposed_as_of) >= proposed_as_of
           AND COALESCE(superseded_as_of, proposed_as_of) >= proposed_as_of),

    -- 🔴 사람 칸을 **빈 문자열로 채우지 않는다.** ''는 "모른다" 를 "있다" 로 위장한다.
    CONSTRAINT ck_logistics_action_proposals_actors
        CHECK (length(btrim(proposed_by)) > 0
           AND (approved_by      IS NULL OR length(btrim(approved_by)) > 0)
           AND (rejected_by      IS NULL OR length(btrim(rejected_by)) > 0)
           AND (rejection_reason IS NULL OR length(btrim(rejection_reason)) > 0)),

    -- 🔴 **영향을 못 댄 제안은 행이 될 수 없다.** Exception 의 근거 CHECK 와 같은 규율
    --    이다 — 다만 «못 쟀다»(UNRESOLVED) 는 정상적인 답이고 여기서 막지 않는다.
    CONSTRAINT ck_logistics_action_proposals_payload
        CHECK (jsonb_typeof(parameters_json) = 'object'
           AND jsonb_typeof(impact_json) = 'object'
           AND jsonb_typeof(evidence_refs_json) = 'array')
);

-- 🔴 **한 문제에 살아 있는 대응안은 하나다.** 응용 코드의 조회가 한 번 빠지는 날에도
--    DB 가 막는다 — 같은 문제에 승인 대기 제안이 둘이면 사람이 무엇을 승인하는지
--    모른다. ⚠️ APPROVED 도 «살아 있다» — 실행(Commit 6)이 끝나야 자리가 빈다.
CREATE UNIQUE INDEX IF NOT EXISTS uq_logistics_action_proposals_live
    ON haetdeul.logistics_action_proposals (sim_run_id, exception_id)
    WHERE status IN ('PROPOSED', 'APPROVED');

-- 🔴 **한 조사가 낸 대응안은 하나다** (Commit 7 보정 · 상세설계 §11).
--    ⚠️ 일반 `UNIQUE (investigation_id)` 가 아니라 **부분** 유일 인덱스인 이유: 손으로
--       세운 제안은 `investigation_id` 가 NULL 이고 그런 행은 여럿이어야 한다. (PostgreSQL
--       의 UNIQUE 도 NULL 을 서로 다르게 보지만, 조건을 적어 두면 «NULL 은 예외» 가
--       우연이 아니라 **의도**라고 읽힌다.)
--
--    🔴 **끝난 제안도 자리를 계속 차지한다.** `previous_proposal_id` 처럼 지워지는 칸이
--       아니라, 제안이 `REJECTED` · `EXPIRED` · `SUPERSEDED` 가 돼도 «그 조사가 이 제안을
--       냈다» 는 사실은 남기 때문이다 — 그래서 거절된 뒤 같은 조사로 새 제안을 세우는
--       길이 여기서 닫힌다.
--
--    ⚠️ **기존 DB 에 이미 중복이 있으면 이 문장이 큰 소리로 실패한다. 그것이 맞다** —
--       조용히 하나를 NULL 로 만들거나 지우지 않는다. 먼저 이렇게 확인한다:
--
--       SELECT investigation_id, count(*)
--       FROM haetdeul.logistics_action_proposals
--       WHERE investigation_id IS NOT NULL
--       GROUP BY investigation_id HAVING count(*) > 1;
CREATE UNIQUE INDEX IF NOT EXISTS uq_logistics_action_proposals_investigation
    ON haetdeul.logistics_action_proposals (investigation_id)
    WHERE investigation_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_logistics_action_proposals_run_status
    ON haetdeul.logistics_action_proposals (sim_run_id, status);

CREATE INDEX IF NOT EXISTS idx_logistics_action_proposals_exception
    ON haetdeul.logistics_action_proposals (sim_run_id, exception_id);

CREATE INDEX IF NOT EXISTS idx_logistics_action_proposals_key
    ON haetdeul.logistics_action_proposals (sim_run_id, proposal_key);

COMMENT ON TABLE haetdeul.logistics_action_proposals IS
    '재고·물류 Agent 가 올린 대응안 한 건과 사람의 결정 (#628 Core). 🔴 APPROVED 는 EXECUTED 가 아니다 — "진행해도 좋다" 는 사람의 결정일 뿐이고 승인 뒤에도 재고·판매·매입은 한 줄도 안 바뀐다(실행은 Commit 6). 한 Exception 에 살아 있는(PROPOSED·APPROVED) 행은 하나다.';

COMMENT ON COLUMN haetdeul.logistics_action_proposals.decision_owner IS
    '승인 뒤 실제 실행 책임을 지는 부서. 🔴 ACTION_DECISION_OWNERS 에서 결정론으로 계산하며 모델이 고르는 칸이 아니다. ⚠️ approved_by(승인한 사람)와 다른 축이다.';

COMMENT ON COLUMN haetdeul.logistics_action_proposals.impact_json IS
    'estimate_action_impact(Commit 3)의 답 그대로. 🔴 제안을 저장하며 다시 셈하는 숫자는 없고, UNRESOLVED 를 지어낸 값으로 메워 FEASIBLE 로 바꾸지 않는다.';

COMMENT ON COLUMN haetdeul.logistics_action_proposals.observed_as_of IS
    '조사가 낸 값 그대로 — 근거 입력들이 알 수 있었던 가장 늦은 날. 🔴 하나라도 관측일이 없으면 NULL 이고 제안일·승인일·현재시각으로 메우지 않는다.';

COMMENT ON COLUMN haetdeul.logistics_action_proposals.proposal_key IS
    'f(sim_run_id, exception_id, action_type, 정규화된 parameters) 지문. 재시도로 같은 제안이 두 번 들어오는 것을 알아보는 용도다. 🔴 유일 제약이 아니다 — 거절된 뒤 같은 안을 다시 올리는 것은 정상이다.';

COMMENT ON COLUMN haetdeul.logistics_action_proposals.executed_as_of IS
    '실제 실행이 **확인된** 시뮬레이션 영업일 (Commit 6). 🔴 approved_as_of 와 함께 서는 것이 정상 흐름이다 — 승인은 끝이 아니라 실행 대기다. ⚠️ 타 부서에 «요청을 접수시켰다» 는 것만으로는 적지 않는다(handoff accepted ≠ executed).';

COMMENT ON COLUMN haetdeul.logistics_action_proposals.failed_as_of IS
    '실행 실패가 **확정된** 날. 🔴 «실행됐는지 모른다» 를 여기 적지 않는다 — 모르는 것을 실패로 적으면 재시도가 이중 실행을 낳는다.';

COMMENT ON COLUMN haetdeul.logistics_action_proposals.executed_by IS
    '실제로 실행을 돌린 주체. ⚠️ decision_owner(실행 책임 부서)와 다른 축이다 — decision_owner=LOGISTICS 인데 executed_by=master-runner 일 수 있다.';

COMMENT ON COLUMN haetdeul.logistics_action_proposals.execution_result_json IS
    '무엇을 실행했고 어느 정본 행을 가리키나 (action · owner · reference_id · result 정도). 🔴 업무 표 snapshot 이 아니고, 여기서 숫자를 새로 만들지 않는다 — 실행 함수의 authoritative 반환이나 DB read-back 값만 담는다. ⚠️ evidence_refs_json(왜 승인했나)과 섞지 않는다.';

COMMENT ON COLUMN haetdeul.logistics_action_proposals.rationale IS
    '모델이 적은 이유 문장. 🔴 업무 사실이 아니라 기록이다 — 숫자와 판정의 주인은 impact_json 이다.';

-- ═══════════════════════════════════════════════════════════════════════════
-- §5  이미 표가 선 DB 를 §3 · §4 와 **같은 자리**로  (#628 Commit 5 close · Commit 7)
--
--     🔴 **`CREATE TABLE IF NOT EXISTS` 안의 제약은 기존 DB 에 안 닿는다.** 표가
--        있으면 그 문장은 통째로 건너뛰므로, 위에서 제약을 더해도 운영 DB 는 예전
--        모양 그대로다. 그래서 같은 제약을 여기서 한 번 더 — 멱등하게 — 건다.
--
--     ⚠️ **`NOT VALID` 를 쓰지 않는다.** `ADD CONSTRAINT` 는 기존 행을 전부 검사하고,
--        축이 어긋난 행이 있으면 **여기서 큰 소리로 실패한다.** 그것이 맞다 —
--        어긋난 장부를 조용히 통과시키고 «제약이 있다» 고 적는 것보다, 마이그레이션이
--        멈추고 사람이 그 행을 보는 편이 낫다.
-- ═══════════════════════════════════════════════════════════════════════════

-- ── 실행 축 칸 (Commit 6) — 기존 DB 에도 같은 모양으로 ────────────────────
ALTER TABLE haetdeul.logistics_action_proposals
    ADD COLUMN IF NOT EXISTS executed_as_of        DATE,
    ADD COLUMN IF NOT EXISTS failed_as_of          DATE,
    ADD COLUMN IF NOT EXISTS executed_by           TEXT,
    ADD COLUMN IF NOT EXISTS execution_result_json JSONB,
    ADD COLUMN IF NOT EXISTS failure_code          TEXT,
    ADD COLUMN IF NOT EXISTS failure_reason        TEXT;

DO $execution_axis$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_logistics_action_proposals_executed'
          AND conrelid = 'haetdeul.logistics_action_proposals'::regclass
    ) THEN
        ALTER TABLE haetdeul.logistics_action_proposals
            ADD CONSTRAINT ck_logistics_action_proposals_executed
            CHECK (status <> 'EXECUTED'
                   OR (approved_as_of IS NOT NULL AND executed_as_of IS NOT NULL
                       AND executed_by IS NOT NULL AND failed_as_of IS NULL));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_logistics_action_proposals_failed'
          AND conrelid = 'haetdeul.logistics_action_proposals'::regclass
    ) THEN
        ALTER TABLE haetdeul.logistics_action_proposals
            ADD CONSTRAINT ck_logistics_action_proposals_failed
            CHECK (status <> 'FAILED'
                   OR (approved_as_of IS NOT NULL AND failed_as_of IS NOT NULL
                       AND failure_code IS NOT NULL AND executed_as_of IS NULL));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_logistics_action_proposals_execution_owner'
          AND conrelid = 'haetdeul.logistics_action_proposals'::regclass
    ) THEN
        ALTER TABLE haetdeul.logistics_action_proposals
            ADD CONSTRAINT ck_logistics_action_proposals_execution_owner
            CHECK (status IN ('EXECUTED', 'FAILED')
                   OR (executed_as_of IS NULL AND failed_as_of IS NULL
                       AND execution_result_json IS NULL AND failure_code IS NULL));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_logistics_action_proposals_approval_provenance'
          AND conrelid = 'haetdeul.logistics_action_proposals'::regclass
    ) THEN
        ALTER TABLE haetdeul.logistics_action_proposals
            ADD CONSTRAINT ck_logistics_action_proposals_approval_provenance
            CHECK (status NOT IN ('APPROVED', 'EXECUTED', 'FAILED')
                   OR (approved_as_of IS NOT NULL AND approved_by IS NOT NULL));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_logistics_action_proposals_execution_exclusive'
          AND conrelid = 'haetdeul.logistics_action_proposals'::regclass
    ) THEN
        ALTER TABLE haetdeul.logistics_action_proposals
            ADD CONSTRAINT ck_logistics_action_proposals_execution_exclusive
            CHECK (executed_as_of IS NULL OR failed_as_of IS NULL);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_logistics_action_proposals_execution_order'
          AND conrelid = 'haetdeul.logistics_action_proposals'::regclass
    ) THEN
        ALTER TABLE haetdeul.logistics_action_proposals
            ADD CONSTRAINT ck_logistics_action_proposals_execution_order
            CHECK ((executed_as_of IS NULL OR executed_as_of >= approved_as_of)
               AND (failed_as_of   IS NULL OR failed_as_of   >= approved_as_of));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_logistics_action_proposals_execution_actor'
          AND conrelid = 'haetdeul.logistics_action_proposals'::regclass
    ) THEN
        ALTER TABLE haetdeul.logistics_action_proposals
            ADD CONSTRAINT ck_logistics_action_proposals_execution_actor
            CHECK ((executed_by   IS NULL OR length(btrim(executed_by)) > 0)
               AND (failure_code  IS NULL OR length(btrim(failure_code)) > 0)
               AND (execution_result_json IS NULL
                    OR jsonb_typeof(execution_result_json) = 'object'));
    END IF;
END
$execution_axis$;

DO $proposal_axis$
BEGIN
    -- 🔴 조사 축 FK (Commit 7). §3 의 표는 `CREATE TABLE IF NOT EXISTS` 로 방금 섰고,
    --    기존 DB 의 제안 표에는 이 제약이 없다.
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'logistics_action_proposals_investigation_axis_fkey'
          AND conrelid = 'haetdeul.logistics_action_proposals'::regclass
    ) THEN
        ALTER TABLE haetdeul.logistics_action_proposals
            ADD CONSTRAINT logistics_action_proposals_investigation_axis_fkey
            FOREIGN KEY (sim_run_id, exception_id, investigation_id)
            REFERENCES haetdeul.logistics_investigations
                       (sim_run_id, exception_id, investigation_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'uq_logistics_action_proposals_axis'
          AND conrelid = 'haetdeul.logistics_action_proposals'::regclass
    ) THEN
        ALTER TABLE haetdeul.logistics_action_proposals
            ADD CONSTRAINT uq_logistics_action_proposals_axis
            UNIQUE (sim_run_id, exception_id, proposal_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'logistics_action_proposals_exception_axis_fkey'
          AND conrelid = 'haetdeul.logistics_action_proposals'::regclass
    ) THEN
        ALTER TABLE haetdeul.logistics_action_proposals
            ADD CONSTRAINT logistics_action_proposals_exception_axis_fkey
            FOREIGN KEY (sim_run_id, exception_id)
            REFERENCES haetdeul.logistics_exceptions (sim_run_id, exception_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'logistics_action_proposals_previous_axis_fkey'
          AND conrelid = 'haetdeul.logistics_action_proposals'::regclass
    ) THEN
        ALTER TABLE haetdeul.logistics_action_proposals
            ADD CONSTRAINT logistics_action_proposals_previous_axis_fkey
            FOREIGN KEY (sim_run_id, exception_id, previous_proposal_id)
            REFERENCES haetdeul.logistics_action_proposals
                       (sim_run_id, exception_id, proposal_id);
    END IF;
END
$proposal_axis$;

COMMENT ON CONSTRAINT ck_logistics_action_proposals_approval_provenance
    ON haetdeul.logistics_action_proposals IS
    '🔴 승인에는 반드시 사람과 날이 있다 — **실행이 상태를 옮긴 뒤에도.** status 가 APPROVED 일 때만 보는 제약은 EXECUTED/FAILED 로 넘어간 순간 «누가 승인했나» 가 비어도 통과시킨다.';

COMMENT ON CONSTRAINT logistics_action_proposals_investigation_axis_fkey
    ON haetdeul.logistics_action_proposals IS
    '🔴 대응안은 **같은 실행 · 같은 문제를 조사한** 기록만 가리킨다 (Commit 7). 없는 조사 ID · RUN-B 의 조사 · EX-B 의 조사를 전부 막는다. ⚠️ investigation_id 가 NULL 이면 검사하지 않는다(MATCH SIMPLE) — 손으로 세운 제안에는 가리킬 조사가 없다. ⚠️ 같은 실행·같은 문제의 **다른 조사**는 이 FK 가 못 막는다 — 그 대조는 proposal_service._check_investigation 이 저장된 snapshot 으로 한다.';

COMMENT ON INDEX haetdeul.uq_logistics_action_proposals_investigation IS
    '🔴 한 조사가 낸 대응안은 **최대 하나다** (Investigation : Proposal = 1:0..1). 한 조사는 한 번 끝난 판단이고 그 판단이 고른 안은 하나다 — 새 안이 필요하면 새 조사를 돌린다. ⚠️ 끝난 제안(REJECTED·EXPIRED·SUPERSEDED)도 자리를 계속 차지한다. investigation_id 가 NULL 인 수동 제안은 여럿 허용된다.';

COMMENT ON CONSTRAINT logistics_action_proposals_exception_axis_fkey
    ON haetdeul.logistics_action_proposals IS
    '🔴 대응안은 **같은 실행의** 문제만 가리킨다. 홑 FK 둘(sim_run_id / exception_id)은 각자 자기 칸만 보므로 «RUN-B 의 제안이 RUN-A 의 문제를 가리키는» 조합을 못 막는다.';

COMMENT ON CONSTRAINT logistics_action_proposals_previous_axis_fkey
    ON haetdeul.logistics_action_proposals IS
    '🔴 대체는 같은 실행 · 같은 문제 안에서만. previous_proposal_id 가 NULL 이면 검사하지 않는다(MATCH SIMPLE) — 첫 제안은 가리킬 앞이 없다.';

COMMIT;
