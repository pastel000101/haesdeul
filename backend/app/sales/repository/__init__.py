"""판매 SQL — **넘겨받은 연결로 실행만 한다.** commit · rollback · 반환을 하지 않는다.

★ 2026-09-29 BL-013 에 만들었다.

연결은 부르는 쪽이 빌린다 — 조회는 `readmodel/` 이 `core_db.read_connection()` 으로, 쓰기는
`service/` 가 `core_db.connection()` + `core_db.transaction(conn)` 으로, 마스터 트랜잭션 안의
원장 기록은 마스터가 넘긴 연결로. 판단과 응답 조립은 여기 두지 않는다.
"""
