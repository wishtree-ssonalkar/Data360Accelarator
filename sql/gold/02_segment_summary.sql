-- description: KPIs per RFM segment and tier: size, revenue, recency, frequency, marketable reach.
-- depends_on: gold.customer_360
CREATE OR REPLACE TABLE gold.segment_summary AS
SELECT
    tenant_id, rfm_segment, tier,
    count(*)                                   AS customers,
    sum(lifetime_revenue)                      AS revenue,
    round(avg(recency_days), 1)                AS avg_recency_days,
    round(avg(orders), 2)                      AS avg_orders,
    round(avg(avg_order_value), 2)             AS avg_order_value,
    count(*) FILTER (WHERE accepts_marketing)  AS marketable_customers
FROM gold.customer_360
GROUP BY ALL;
