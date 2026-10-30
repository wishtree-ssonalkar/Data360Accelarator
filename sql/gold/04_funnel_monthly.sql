-- description: Web/app conversion funnel per month and device (sessions -> product view -> cart -> checkout -> purchase).
-- depends_on: silver.web_sessions
CREATE OR REPLACE TABLE gold.funnel_monthly AS
SELECT
    tenant_id,
    CAST(date_trunc('month', started_at) AS DATE)            AS month,
    device,
    count(*)                                                 AS sessions,
    count(*) FILTER (WHERE viewed_product)                   AS product_views,
    count(*) FILTER (WHERE added_to_cart)                    AS add_to_carts,
    count(*) FILTER (WHERE started_checkout)                 AS checkouts,
    count(*) FILTER (WHERE purchased)                        AS purchases,
    count(*) FILTER (WHERE customer_id IS NOT NULL)          AS identified_sessions
FROM silver.web_sessions
GROUP BY ALL;
