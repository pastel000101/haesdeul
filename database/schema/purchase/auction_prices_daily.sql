-- auction_prices_daily — 매입
--
-- 2026-09-30 BL-021: 아래 출처에서 이 객체의 문장만 **그대로** 옮겼다(문장 · 순서 불변).
--   옛 `database/10_domain_schema.sql` (2026-08-30 test DB 에서 뜬 pg_dump 스냅샷)
-- 옛 파일 전체와 머리말은 git `3525c8f3` 에 있다. 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.auction_prices_daily (
    id bigint,
    auction_date date,
    market_category character varying(10),
    wholesale_market_code character varying(20),
    wholesale_market_name character varying(100),
    item_code character varying(20),
    item_name character varying(50),
    grade_code character varying(20),
    grade_name character varying(50),
    avg_auction_price_krw_per_kg numeric(28,6),
    min_auction_price_krw_per_kg numeric(28,6),
    max_auction_price_krw_per_kg numeric(28,6),
    trade_volume_kg numeric(28,6),
    trade_amount_krw numeric(28,6),
    package_trade_quantity numeric(28,6),
    source_trade_count bigint,
    source character varying(200),
    subclass_code character varying(20),
    subclass_name character varying(100),
    package_code character varying(20),
    package_name character varying(50),
    unit_weight_kg numeric(18,3)
);

COMMIT;
