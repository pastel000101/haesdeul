-- 새 DB 를 세울 때 가장 먼저 적용된다 — 순서는 `database/new_database_order.txt` 첫 줄이 정한다
-- (옛 `00_` 파일 이름 정렬로 정하던 것은 2026-09-30 재구성 BL-021 에서 적용 목록으로 바뀌었다).
-- 나머지 *_agent_runs.sql 은 haetdeul 스키마가 있어야 테이블을 만들 수 있다.
CREATE SCHEMA IF NOT EXISTS haetdeul;
