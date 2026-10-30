-- description: All orders from every sales source, with order value from lines (or the order total) and the resolved customer_id.
-- depends_on: silver.std_orders, silver.std_order_lines, silver.identity_keys
CREATE OR REPLACE TABLE silver.transactions AS
WITH lines AS (
    SELECT tenant_id, order_id, sum(line_amount) AS lines_value, sum(quantity) AS items
    FROM silver.std_order_lines GROUP BY ALL
)
SELECT
    o.tenant_id,
    o.order_id,
    k.customer_id,
    o.source_name                                         AS source,
    o.order_ts,
    CAST(o.order_ts AS DATE)                              AS order_date,
    o.channel,
    o.store_code,
    lower(o.status)                                       AS status,
    coalesce(l.items, 0)                                  AS items,
    coalesce(l.lines_value, o.order_value, 0)             AS order_value,
    CASE WHEN lower(o.status) = 'completed' THEN coalesce(l.lines_value, o.order_value, 0) ELSE 0 END AS net_revenue
FROM silver.std_orders o
LEFT JOIN lines l USING (tenant_id, order_id)
LEFT JOIN silver.identity_keys k ON k.tenant_id = o.tenant_id AND k.identity_key = o.identity_key;
