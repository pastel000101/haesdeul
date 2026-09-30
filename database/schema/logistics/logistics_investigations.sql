-- logistics_investigations — 물류
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/40_logistics_agent_schema.sql` (2026-09-12 · 09-13 물류 Agent Core)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

-- ═══════════════════════════════════════════════════════════════════════════
-- §3  조사 실행 — "이 문제를 언제 조사했고 무엇으로 끝났나"  (#628 Commit 7)
--
--     🔴 **끝난 실행의 기록이다 — 상태표가 아니다.**
--
--     ```text
--     Exception      지금 참인 조건       상태가 오간다 (OPEN ↔ PROPOSED → RESOLVED)
--     Investigation  조사 **한 번**       🔴 한 번 적히면 안 바뀐다
--     Proposal       그 조사가 낸 대응안   상태가 오간다 (PROPOSED → APPROVED → …)
--     ```
--
--        그래서 이 표에는 상태 칸도 결정 칸도 없다. 다시 조사했으면 **새 행**이다
--        (`UPDATE result` · `UPDATE finish_reason` 을 하는 Production 코드가 없다).
--
--     🔴 **Tool 답 창고가 아니다.** `tool_trace_json` 은 *"무엇을 어떤 순서로 물었고
--        그 호출이 어떻게 끝났나"* 만 담는다 — Tool 이 낸 답 전체는 **안 들어온다.**
--        용량 문맥의 18일 창 · 품목 Lot 목록 · 예약 객체를 조사마다 통째로 복사하면
--        이 표가 감사 기록이 아니라 조회 캐시가 된다 (Commit 5 가 제안의 근거 칸에
--        내린 결정과 같다).
--
--     🔴 **같은 문제를 같은 날 두 번 조사할 수 있다.** 그래서
--        `(sim_run_id, exception_id, as_of)` 에 유일 제약을 **안 건다** — 두 번
--        조사했으면 두 번 실행한 것이고, 그 둘은 Tool 상황도 LLM 판단도 다를 수 있다.
--        «중복» 으로 접으면 나중 조사가 앞 조사를 덮어 실행 이력이 사라진다.
--
--     ⚠️ **기존 `logistics_agent_runs` 를 늘려 쓰지 않았다.** 저 표는 Logistics API 의
--        Request/Response 실행이력이다 — `cycle ∈ PROCUREMENT·SALES` ·
--        `request_payload`/`response_payload` · 쓰는 자리는 `app/logistics/service.py`
--        하나. 조사는 «Exception 하나 → Tool 조사 → 후보 action» 이라 축이 다르고,
--        `cycle` 에 `INVESTIGATION` 을 끼워 넣으면 그 표의 뜻이 깨진다.
--
--     ⚠️ **범용 `agent_runs` 도 못 쓴다.** ① `exception_id` 칸이 없어 §4 가 요구하는
--        «같은 실행 · 같은 문제» 복합 FK 의 대상이 될 수 없고, ② 상태 칸이
--        `run_status` 하나뿐이라 `finish_reason`(그래프가 어디서 끝났나)과
--        `llm_status`(AI 가 실제로 판단했나)를 한 칸에 뭉개야 하며, ③ **남의 표다** —
--        매입 제안과 검토가 FK 로 매달려 있다.
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS haetdeul.logistics_investigations (
    -- INV-{uuid}. 🔴 **번호를 세서 짓지 않는다** — `MAX(n)+1` 은 동시에 두 조사가
    --    시작하면 같은 이름을 낸다. 조사는 사람이 부를 때마다 서므로 채번이 경합한다.
    investigation_id      TEXT NOT NULL,
    -- 🔴 리셋 축. 이 칸이 있어서 `reset_sim_run_ledger` 가 표를 **자동으로 발견**해
    --    `--reset` 때 지운다 (`master/sim_run_open.py` `_axis_tables`).
    sim_run_id            TEXT NOT NULL,
    exception_id          TEXT NOT NULL,
    -- 시뮬레이션 영업일. 🔴 `created_at`(벽시계)과 다른 축이고, 조사 Runtime 이 받은
    --    `as_of` 그대로다 — `date.today()` · `created_at::date` 로 대체하지 않는다.
    as_of                 DATE NOT NULL,

    -- 🔴 **두 축을 한 칸에 안 담는다.** 그래야 *"규칙 제안인데 조사는 정상 종료"*
    --    같은 흔한 경우가 적힌다.
    --    finish_reason  그래프가 **어디서** 끝났나
    --    llm_status     AI 가 **실제로** 판단했나
    finish_reason         TEXT NOT NULL,
    llm_status            TEXT NOT NULL,
    -- 공급자 전송이 최종 실패한 원인 분류. 성공했거나 애초에 안 보냈으면 NULL 이다.
    llm_error_kind        TEXT,

    -- 🔴 조사가 낸 값 **그대로**. `None` 이면 NULL 이다 — `as_of` · `created_at` 으로
    --    메우지 않는다 (상세설계 §18 · 제안 표와 같은 규율).
    observed_as_of        DATE,

    -- [{sequence, tool_name, arguments, status, reason, detail, observed_as_of,
    --   uncertainties}, …]
    -- 🔴 **`answer` 키가 없다.** 이 칸이 답하는 것은 *"무엇을 시도했나"* 이지
    --    *"그 답이 무엇이었나"* 가 아니다.
    -- ⚠️ 제안의 `evidence_refs_json`(왜 이 대응안을 올렸나 · 핵심 facts)과 **다른
    --    칸이다.** 둘을 같은 payload 로 만들지 않는다.
    tool_trace_json       JSONB NOT NULL,
    -- 조사의 **최종 판단** — 요약 · 발견 · 못 본 것 · 후보와 그 영향 · 추천 번호.
    -- 🔴 여기서 숫자를 새로 만들지 않는다. `options[].impact` 는 Commit 3 의
    --    결정론 계산기가 낸 답 그대로이고, 저장하며 다시 셈하는 값은 하나도 없다.
    result_json           JSONB NOT NULL,

    -- 🔴 DB 벽시계. 감사용이고 **영업 판단에 쓰지 않는다.**
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT logistics_investigations_pkey PRIMARY KEY (investigation_id),
    -- 🔴 §4 의 복합 FK 가 가리킬 자리. PK 가 이미 유일성을 주지만 복합 FK 는
    --    «유일한 칸 묶음» 만 가리킬 수 있다.
    -- ⚠️ **이것이 «같은 날 한 번» 제약은 아니다.** `investigation_id` 가 묶음에 들어
    --    있어서 같은 문제를 같은 날 두 번 조사해도 둘 다 선다.
    CONSTRAINT uq_logistics_investigations_axis
        UNIQUE (sim_run_id, exception_id, investigation_id),

    CONSTRAINT logistics_investigations_sim_run_id_fkey
        FOREIGN KEY (sim_run_id) REFERENCES haetdeul.sim_runs(sim_run_id),
    -- 🔴 **조사는 «같은 실행의» 문제만 가리킨다.** 홑 FK 둘로는 «RUN-B 의 조사가
    --    RUN-A 의 문제를 가리키는» 조합을 못 막는다 — 각자 자기 칸만 보기 때문이다.
    CONSTRAINT logistics_investigations_exception_axis_fkey
        FOREIGN KEY (sim_run_id, exception_id)
        REFERENCES haetdeul.logistics_exceptions (sim_run_id, exception_id),

    -- 🔴 어휘는 닫혀 있다 — `FinishReason` · `LLMStatus` 와 **글자 그대로** 같다.
    CONSTRAINT ck_logistics_investigations_finish_reason
        CHECK (finish_reason IN ('FINISHED', 'NOT_FOUND', 'TOOL_FAILED',
                                 'BUDGET_EXCEEDED', 'GUARD_EXHAUSTED', 'TIMEOUT',
                                 'LLM_FAILED')),
    CONSTRAINT ck_logistics_investigations_llm_status
        CHECK (llm_status IN ('SUCCESS', 'SKIPPED_TEMPLATE', 'FALLBACK', 'DISABLED')),
    -- 🔴 «모른다» 를 빈 문자열로 적지 않는다. 안 실패했으면 NULL 이다.
    CONSTRAINT ck_logistics_investigations_llm_error_kind
        CHECK (llm_error_kind IS NULL OR length(btrim(llm_error_kind)) > 0),
    -- 🔴 **모양이 틀린 감사 기록은 행이 될 수 없다.** trace 는 배열, 결과는 객체다.
    CONSTRAINT ck_logistics_investigations_payload
        CHECK (jsonb_typeof(tool_trace_json) = 'array'
           AND jsonb_typeof(result_json) = 'object')
);

-- 🔴 **유일 인덱스가 아니다.** 한 문제를 며칠에 걸쳐 여러 번 조사한 이력을 그대로
--    읽기 위한 것이다 (같은 날 두 행도 정상이다).
CREATE INDEX IF NOT EXISTS idx_logistics_investigations_exception
    ON haetdeul.logistics_investigations (sim_run_id, exception_id, as_of);

CREATE INDEX IF NOT EXISTS idx_logistics_investigations_run_as_of
    ON haetdeul.logistics_investigations (sim_run_id, as_of);

COMMENT ON TABLE haetdeul.logistics_investigations IS
    '재고·물류 Agent 가 Exception 하나를 **실제로 조사한 한 번** (#628 Commit 7). 🔴 끝난 실행의 감사 기록이라 상태 칸도 결정 칸도 없고, 다시 조사하면 새 행이다. 🔴 Tool 답 전체를 담지 않는다 — tool_trace_json 은 "무엇을 어떤 순서로 물었나" 까지다. ⚠️ 기존 logistics_agent_runs(Logistics API Request/Response 이력)와 다른 축이고, 범용 agent_runs 는 exception_id 칸이 없어 대응안의 복합 FK 대상이 될 수 없다.';

COMMENT ON COLUMN haetdeul.logistics_investigations.tool_trace_json IS
    '[{sequence, tool_name, arguments, status, reason, detail, observed_as_of, uncertainties}, …]. 🔴 answer 키가 없다 — 이 칸은 "무엇을 시도했나" 이지 "그 답이 무엇이었나" 가 아니다. ⚠️ 제안의 evidence_refs_json(왜 승인 대상으로 올렸나)과 다른 칸이다.';

COMMENT ON COLUMN haetdeul.logistics_investigations.result_json IS
    '조사의 최종 판단 — 요약 · 발견 · 못 본 것 · 후보와 그 영향 · 추천 번호. 🔴 저장하며 다시 셈하는 숫자가 하나도 없다: options[].impact 는 Commit 3 의 결정론 계산기가 낸 답 그대로다. 🔴 raw LLM prompt/response · system prompt · 공급자 payload 는 담지 않는다.';

COMMENT ON COLUMN haetdeul.logistics_investigations.finish_reason IS
    '그래프가 **어디서** 끝났나 (FINISHED · NOT_FOUND · TOOL_FAILED · BUDGET_EXCEEDED · GUARD_EXHAUSTED · TIMEOUT · LLM_FAILED). ⚠️ llm_status(AI 가 실제로 판단했나)와 다른 축이다 — 한 칸에 뭉개지 않는다.';

COMMENT ON COLUMN haetdeul.logistics_investigations.observed_as_of IS
    '조사가 낸 값 그대로 — 근거 입력들이 알 수 있었던 가장 늦은 날. 🔴 하나라도 관측일이 없으면 NULL 이고 as_of · created_at 으로 메우지 않는다.';

COMMIT;
