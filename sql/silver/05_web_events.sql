-- description: Behavioural events linked to customers, plus one row per browsing session with funnel depth.
-- depends_on: silver.std_events, silver.identity_keys
CREATE OR REPLACE TABLE silver.web_events AS
SELECT
    e.tenant_id, e.source_name AS source, e.event_id, e.event_type, e.event_ts, e.email_norm,
    e.session_id, e.device, e.sku, e.order_id,
    k.customer_id
FROM silver.std_events e
LEFT JOIN silver.identity_keys k ON k.tenant_id = e.tenant_id AND k.identity_key = e.identity_key;

CREATE OR REPLACE TABLE silver.web_sessions AS
SELECT
    tenant_id,
    session_id,
    any_value(customer_id) FILTER (WHERE customer_id IS NOT NULL) AS customer_id,
    min(event_ts)                                                 AS started_at,
    any_value(device)                                             AS device,
    count(*)                                                      AS events,
    bool_or(event_type = 'product_view')                          AS viewed_product,
    bool_or(event_type = 'add_to_cart')                           AS added_to_cart,
    bool_or(event_type = 'checkout_start')                        AS started_checkout,
    bool_or(event_type = 'purchase')                              AS purchased
FROM silver.web_events
GROUP BY tenant_id, session_id;
