-- description: Golden customer record: one row per resolved person; each attribute taken from the highest-priority source that has it.
-- depends_on: silver.identity_map, silver.std_customers, meta.sources
CREATE OR REPLACE TABLE silver.identity_keys AS
SELECT DISTINCT tenant_id, source_key AS identity_key, customer_id FROM silver.identity_map;

CREATE OR REPLACE TABLE silver.customers AS
WITH c AS (
    SELECT s.*, k.customer_id,
           -- lower = better: source priority first, then the oldest record
           coalesce(p.priority, 100) * 1e10 + coalesce(epoch(s.created_at), 9e9) AS rank_key,
           coalesce(s.full_name, nullif(trim(coalesce(s.first_name, '') || ' ' || coalesce(s.last_name, '')), '')) AS name
    FROM silver.std_customers s
    JOIN silver.identity_keys k USING (tenant_id, identity_key)
    LEFT JOIN meta.sources p ON p.tenant_id = s.tenant_id AND p.source_name = s.source_name
), best AS (
    SELECT tenant_id, customer_id,
           arg_min(name, rank_key)        FILTER (WHERE name IS NOT NULL)        AS full_name,
           arg_min(email_norm, rank_key)  FILTER (WHERE email_norm IS NOT NULL)  AS email,
           arg_min(phone_norm, rank_key)  FILTER (WHERE phone_norm IS NOT NULL)  AS phone,
           arg_min(city, rank_key)        FILTER (WHERE city IS NOT NULL)        AS city,
           arg_min(region, rank_key)      FILTER (WHERE region IS NOT NULL)      AS region,
           arg_min(lead_source, rank_key) FILTER (WHERE lead_source IS NOT NULL) AS lead_source,
           bool_or(accepts_marketing)                                            AS accepts_marketing,
           CAST(min(created_at) AS DATE)                                         AS registered_on,
           count(*)                                                              AS account_records
    FROM c GROUP BY ALL
), ids AS (
    SELECT tenant_id, customer_id,
           min(email_norm)                                          AS any_email,
           min(phone_norm)                                          AS any_phone,
           string_agg(DISTINCT source_system, ', ' ORDER BY source_system) AS sources,
           count(DISTINCT source_key)                               AS source_records,
           any_value(match_rule)                                    AS match_rule
    FROM silver.identity_map GROUP BY ALL
)
SELECT
    ids.tenant_id,
    ids.customer_id,
    coalesce(best.full_name, 'Guest ' || right(ids.customer_id, 4)) AS full_name,
    coalesce(best.email, ids.any_email)                             AS email,
    coalesce(best.phone, ids.any_phone)                             AS phone,
    best.city,
    best.region,
    best.lead_source,
    coalesce(best.accepts_marketing, false)                         AS accepts_marketing,
    coalesce(best.account_records, 0)                               AS account_records,
    best.registered_on,
    ids.sources,
    ids.source_records,
    ids.match_rule
FROM ids LEFT JOIN best USING (tenant_id, customer_id);
