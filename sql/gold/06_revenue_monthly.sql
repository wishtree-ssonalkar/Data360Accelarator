-- description: Monthly orders, customers and revenue by channel, new vs returning, and current segment/tier.
-- depends_on: silver.transactions, gold.customer_360
CREATE OR REPLACE TABLE gold.revenue_monthly AS
SELECT
    t.tenant_id,
    CAST(date_trunc('month', t.order_date) AS DATE)                     AS month,
    t.channel,
    CASE WHEN t.customer_id IS NULL THEN 'Unidentified'
         WHEN t.order_date = c.first_order_date THEN 'New'
         ELSE 'Returning' END                                           AS customer_type,
    coalesce(c.rfm_segment, 'Unidentified')                             AS rfm_segment,
    coalesce(c.tier, 'Unidentified')                                    AS tier,
    count(*) FILTER (WHERE t.status = 'completed')                      AS orders,
    count(DISTINCT t.customer_id) FILTER (WHERE t.status = 'completed') AS customers,
    sum(t.net_revenue)                                                  AS net_revenue,
    coalesce(sum(t.order_value) FILTER (WHERE t.status = 'refunded'), 0) AS refunded_amount
FROM silver.transactions t
LEFT JOIN gold.customer_360 c USING (tenant_id, customer_id)
GROUP BY ALL;
