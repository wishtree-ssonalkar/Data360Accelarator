-- description: Support tickets from every helpdesk source, linked to the resolved customer_id.
-- depends_on: silver.std_tickets, silver.identity_keys
CREATE OR REPLACE TABLE silver.support_tickets AS
SELECT
    t.tenant_id, t.source_name AS source, t.ticket_id, t.email_norm, t.created_at, t.issue_type,
    lower(t.status) AS status, t.priority, t.csat, t.sku, t.channel,
    k.customer_id,
    lower(t.status) IN ('solved', 'closed', 'resolved') AS is_solved
FROM silver.std_tickets t
LEFT JOIN silver.identity_keys k ON k.tenant_id = t.tenant_id AND k.identity_key = t.identity_key;
