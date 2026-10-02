-- v_current_partner_demand — 매입
--
-- 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE VIEW haetdeul.v_current_partner_demand AS
 SELECT p.partner_id,
    p.partner_name,
    p.order_cycle_days,
    sum(d.daily_demand_kg) AS daily_total_demand_kg,
    (sum(d.daily_demand_kg) * (p.order_cycle_days)::numeric) AS baseline_order_qty_kg,
    p.sales_collection_days,
    p.pricing_contract_type
   FROM (haetdeul.partners p
     JOIN haetdeul.partner_item_demands d ON ((d.partner_id = p.partner_id)))
  WHERE (p.active = true)
  GROUP BY p.partner_id, p.partner_name, p.order_cycle_days, p.sales_collection_days, p.pricing_contract_type;

COMMENT ON VIEW haetdeul.v_current_partner_demand IS '활성 고객의 일수요와 발주주기로 기준 주문량을 계산한 View.';

COMMIT;
