-- v_zone_position_occupancy — 물류
--
-- 적용 순서는 `database/new_database_order.txt`.

BEGIN;

-- ═══════════════════════════════════════════════════════════════════════════
-- §10  물류 전용 View
-- ═══════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE VIEW haetdeul.v_zone_position_occupancy AS
 SELECT z.zone_id,
    z.zone_code,
    z.zone_kind,
    z.purpose,
    COALESCE(cap.max_pallet_positions, 0::bigint) AS max_pallet_positions,
    COALESCE(occ.occupied_pallet_count, 0::bigint) AS occupied_pallet_count,
    COALESCE(cap.max_pallet_positions, 0::bigint) - COALESCE(occ.occupied_pallet_count, 0::bigint) AS free_pallet_positions_now
   FROM haetdeul.warehouse_zones z
     LEFT JOIN ( SELECT storage_locations.zone_id,
            count(*) AS max_pallet_positions
           FROM haetdeul.storage_locations
          WHERE storage_locations.is_active
          GROUP BY storage_locations.zone_id) cap ON cap.zone_id = z.zone_id
     LEFT JOIN ( SELECT l.zone_id,
            count(*) AS occupied_pallet_count
           FROM haetdeul.pallets p
             JOIN haetdeul.storage_locations l ON l.location_id = p.current_location_id
          WHERE p.status = ANY (ARRAY['ACTIVE'::text, 'HOLD'::text])
          GROUP BY l.zone_id) occ ON occ.zone_id = z.zone_id
  WHERE z.is_active;

COMMENT ON VIEW haetdeul.v_zone_position_occupancy IS
    'Zone 별 Pallet Position 정원과 현재 점유. 날짜별 free_positions 투영은 코드 몫이다 (01 §11~§12).';

COMMIT;
