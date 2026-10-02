-- v_move_line_integrity — 물류
--
-- 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE OR REPLACE VIEW haetdeul.v_move_line_integrity AS
 SELECT m.move_id,
    m.sim_run_id,
    m.lot_id,
    m.move_type,
    m.moved_at,
    m.quantity_kg AS header_qty_kg,
    sum(l.quantity_kg) AS line_total_kg,
    sum(l.quantity_kg) - m.quantity_kg AS diff_kg,
    count(*) AS line_count
   FROM haetdeul.inventory_moves m
     JOIN haetdeul.inventory_move_lines l ON l.move_id = m.move_id
  GROUP BY m.move_id, m.sim_run_id, m.lot_id, m.move_type, m.moved_at, m.quantity_kg
 HAVING sum(l.quantity_kg) <> m.quantity_kg;

COMMENT ON VIEW haetdeul.v_move_line_integrity IS
    '원장 정합성 검출 — 비어 있어야 정상이다. Header 수량과 Move Line 합계가 갈린 Move 를 낸다 (02 §14 INVENTORY_INTEGRITY_ERROR). Line 이 0건인 Move 는 대상이 아니다 — Pallet 확정 전 입고(02 §9)가 그 상태다. 🔴 DB 는 이것을 막지 않는다. Service 가 검사한다.';

COMMIT;
