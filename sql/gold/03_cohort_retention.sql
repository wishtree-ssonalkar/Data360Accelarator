-- description: Monthly acquisition cohorts and the share of each cohort that bought again N months later.
-- depends_on: silver.transactions
CREATE OR REPLACE TABLE gold.cohort_retention AS
WITH firsts AS (
    SELECT tenant_id, customer_id, date_trunc('month', min(order_date)) AS cohort_month
    FROM silver.transactions WHERE customer_id IS NOT NULL AND status = 'completed' GROUP BY ALL
), activity AS (
    SELECT DISTINCT tenant_id, customer_id, date_trunc('month', order_date) AS active_month
    FROM silver.transactions WHERE customer_id IS NOT NULL AND status = 'completed'
), sizes AS (
    SELECT tenant_id, cohort_month, count(*) AS cohort_size FROM firsts GROUP BY ALL
)
SELECT
    f.tenant_id,
    CAST(f.cohort_month AS DATE)                              AS cohort_month,
    date_diff('month', f.cohort_month, a.active_month)        AS months_since_first,
    s.cohort_size,
    count(DISTINCT a.customer_id)                             AS active_customers,
    round(count(DISTINCT a.customer_id) / any_value(s.cohort_size), 4) AS retention_rate
FROM firsts f
JOIN activity a USING (tenant_id, customer_id)
JOIN sizes s    USING (tenant_id, cohort_month)
WHERE date_diff('month', f.cohort_month, a.active_month) <= 12
GROUP BY 1, 2, 3, 4;
