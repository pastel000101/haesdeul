-- evidences — 공통
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/10_domain_schema.sql` (2026-08-30 test DB 에서 뜬 pg_dump 스냅샷)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.evidences (
    evidence_id text NOT NULL,
    source_ref text NOT NULL,
    evidence_type text NOT NULL,
    source_name text NOT NULL,
    source_uri text,
    claim text,
    source_as_of text,
    status text NOT NULL,
    note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

COMMENT ON TABLE haetdeul.evidences IS '프로젝트 전체 근거 원장. Persona·시장데이터·정책값·Agent 판단이 참조하는 source_ref와 출처를 관리한다.';

COMMENT ON COLUMN haetdeul.evidences.evidence_id IS '연결된 근거 원장 ID.';

COMMENT ON COLUMN haetdeul.evidences.source_ref IS '근거/정책의 안정적 참조 ID.';

COMMENT ON COLUMN haetdeul.evidences.evidence_type IS '근거 성격/유형.';

COMMENT ON COLUMN haetdeul.evidences.source_name IS '근거 문서/기관/데이터셋명.';

COMMENT ON COLUMN haetdeul.evidences.source_uri IS '근거 원문 URL 또는 파일 식별정보.';

COMMENT ON COLUMN haetdeul.evidences.claim IS '근거가 뒷받침하는 값/조건 요약.';

COMMENT ON COLUMN haetdeul.evidences.source_as_of IS '근거 기준일/조회일/적용기간.';

COMMENT ON COLUMN haetdeul.evidences.status IS '상태값.';

COMMENT ON COLUMN haetdeul.evidences.note IS '추가 설명 및 주의사항.';

COMMENT ON COLUMN haetdeul.evidences.created_at IS '생성시각.';

ALTER TABLE ONLY haetdeul.evidences
    ADD CONSTRAINT evidences_pkey PRIMARY KEY (evidence_id);

ALTER TABLE ONLY haetdeul.evidences
    ADD CONSTRAINT evidences_source_ref_key UNIQUE (source_ref);

COMMIT;
