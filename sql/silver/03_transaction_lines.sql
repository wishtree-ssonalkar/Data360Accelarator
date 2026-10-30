-- description: Order lines enriched with product, cost, customer and order status.
-- depends_on: silver.std_order_lines, silver.transactions, silver.products
CREATE OR REPLACE TABLE silver.transaction_lines AS
SELECT
    l.tenant_id,
    l.line_id,
    l.order_id,
    t.customer_id,
    t.order_date,
    t.channel,
    t.status,
    l.sku,
    p.product_name,
    p.category,
    l.quantity,
    l.line_amount,
    p.unit_cost,
    CASE WHEN t.status = 'completed' THEN l.line_amount ELSE 0 END AS net_line_amount,
    CASE WHEN t.status = 'completed' THEN l.line_amount - coalesce(p.unit_cost, 0) * l.quantity ELSE 0 END AS gross_margin
FROM silver.std_order_lines l
JOIN silver.transactions t USING (tenant_id, order_id)
LEFT JOIN silver.products p USING (tenant_id, sku);
