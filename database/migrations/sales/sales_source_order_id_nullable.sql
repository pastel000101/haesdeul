-- ★ 2026-09-30 BL-021 보완 — 새 DB 는 이 파일을 적용하지 않는다. 이 변경의 최종 모양은
--   `database/schema/sales/sales.sql` 에 있고, 이 파일은 이미 쓰는 DB 를 갱신할 때만 쓴다
--   (`database/README.md` §2). 아래 머리말의 신규 구축 · 적용 순서 안내는 작성 당시 기준이다.
--
-- sales.source_order_id NOT NULL 해제 (2026-09-07 · Sales)
--
-- 신규 구축 DB는 `database/10_domain_schema.sql` 이 정본이다.
-- 이 파일은 기존 dev/shared DB 에만 적용한다.
-- 행 데이터는 바꾸지 않고 nullable 제약만 완화한다.
-- PostgreSQL 에서는 이미 nullable 인 칸에 대해 반복 실행해도 안전하다.

BEGIN;

ALTER TABLE haetdeul.sales
    ALTER COLUMN source_order_id DROP NOT NULL;

COMMIT;
