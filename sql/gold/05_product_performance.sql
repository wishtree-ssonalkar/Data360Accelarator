-- description: Per-product sales, margin, refund rate, buyers, support complaints, CSAT, views and carts.
-- depends_on: silver.products, silver.transaction_lines, silver.support_tickets, silver.web_events
CREATE OR REPLACE TABLE gold.product_performance AS
WITH sales AS (
    SELECT tenant_id, sku,
           sum(quantity) FILTER (WHERE status = 'completed')                    AS units_sold,
           count(DISTINCT order_id) FILTER (WHERE status = 'completed')         AS orders,
           count(DISTINCT order_id) FILTER (WHERE status = 'refunded')          AS refunded_orders,
           count(DISTINCT customer_id) FILTER (WHERE status = 'completed')      AS buyers,
           sum(net_line_amount)                                                 AS net_revenue,
           sum(gross_margin)                                                    AS gross_margin,
           sum(line_amount) FILTER (WHERE status = 'refunded')                  AS refunded_amount
    FROM silver.transaction_lines GROUP BY ALL
), tix AS (
    SELECT tenant_id, sku, count(*) AS support_tickets, round(avg(csat), 2) AS avg_ticket_csat
    FROM silver.support_tickets WHERE sku IS NOT NULL GROUP BY ALL
), views AS (
    SELECT tenant_id, sku,
           count(*) FILTER (WHERE event_type = 'product_view') AS product_views,
           count(*) FILTER (WHERE event_type = 'add_to_cart')  AS add_to_carts
    FROM silver.web_events WHERE sku IS NOT NULL GROUP BY ALL
)
SELECT
    p.tenant_id, p.sku, p.product_name, p.category, p.list_price, p.is_active,
    coalesce(s.units_sold, 0)        AS units_sold,
    coalesce(s.orders, 0)            AS orders,
    coalesce(s.buyers, 0)            AS buyers,
    coalesce(s.net_revenue, 0)       AS net_revenue,
    coalesce(s.gross_margin, 0)      AS gross_margin,
    CASE WHEN s.net_revenue > 0 THEN round(s.gross_margin / s.net_revenue, 4) END AS margin_pct,
    coalesce(s.refunded_amount, 0)   AS refunded_amount,
    round(coalesce(s.refunded_orders, 0) / nullif(coalesce(s.orders, 0) + coalesce(s.refunded_orders, 0), 0), 4) AS refund_rate,
    coalesce(t.support_tickets, 0)   AS support_tickets,
    t.avg_ticket_csat,
    coalesce(v.product_views, 0)     AS product_views,
    coalesce(v.add_to_carts, 0)      AS add_to_carts
FROM silver.products p
LEFT JOIN sales s USING (tenant_id, sku)
LEFT JOIN tix t   USING (tenant_id, sku)
LEFT JOIN views v USING (tenant_id, sku);
