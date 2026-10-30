-- description: One row per customer: profile, purchase behaviour, support, engagement, RFM scores, segment, tier, churn risk.
-- depends_on: silver.customers, silver.transactions, silver.transaction_lines, silver.support_tickets, silver.web_sessions, meta.business_rules
CREATE OR REPLACE TABLE gold.customer_360 AS
WITH r AS (
    SELECT * FROM meta.business_rules
), orders AS (
    SELECT tenant_id, customer_id,
           count(*) FILTER (WHERE status = 'completed')               AS orders,
           count(*) FILTER (WHERE status = 'refunded')                AS refunded_orders,
           sum(net_revenue)                                           AS lifetime_revenue,
           min(order_date) FILTER (WHERE status = 'completed')        AS first_order_date,
           max(order_date) FILTER (WHERE status = 'completed')        AS last_order_date,
           mode(channel) FILTER (WHERE status = 'completed')          AS preferred_channel,
           count(DISTINCT channel)                                    AS channels_used
    FROM silver.transactions WHERE customer_id IS NOT NULL GROUP BY ALL
), fav AS (
    SELECT tenant_id, customer_id, arg_max(category, revenue) AS favourite_category
    FROM (SELECT tenant_id, customer_id, category, sum(net_line_amount) AS revenue
          FROM silver.transaction_lines WHERE customer_id IS NOT NULL AND status = 'completed' GROUP BY ALL)
    GROUP BY ALL
), tix AS (
    SELECT tenant_id, customer_id,
           count(*)                                AS support_tickets,
           count(*) FILTER (WHERE NOT is_solved)   AS open_tickets,
           round(avg(csat), 2)                     AS avg_csat,
           max(created_at)                         AS last_ticket_at
    FROM silver.support_tickets WHERE customer_id IS NOT NULL GROUP BY ALL
), web AS (
    SELECT s.tenant_id, s.customer_id,
           count(*)                                                                      AS web_sessions,
           count(*) FILTER (WHERE s.started_at >= r.as_of_date - CAST(r.recent_activity_days AS INTEGER)) AS web_sessions_recent,
           count(*) FILTER (WHERE s.added_to_cart AND NOT s.purchased)                   AS abandoned_carts,
           min(s.started_at)                                                             AS first_session_at,
           max(s.started_at)                                                             AS last_session_at
    FROM silver.web_sessions s JOIN r USING (tenant_id)
    WHERE s.customer_id IS NOT NULL GROUP BY ALL
), base AS (
    SELECT
        c.*,
        coalesce(o.orders, 0)                                         AS orders,
        coalesce(o.refunded_orders, 0)                                AS refunded_orders,
        coalesce(o.lifetime_revenue, 0)                               AS lifetime_revenue,
        CASE WHEN o.orders > 0 THEN round(o.lifetime_revenue / o.orders, 2) END AS avg_order_value,
        o.first_order_date,
        o.last_order_date,
        o.preferred_channel,
        coalesce(o.channels_used, 0)                                  AS channels_used,
        f.favourite_category,
        date_diff('day', o.last_order_date, r.as_of_date)             AS recency_days,
        CASE WHEN o.orders >= 2
             THEN round(date_diff('day', o.first_order_date, o.last_order_date) / (o.orders - 1), 1) END
                                                                      AS avg_days_between_orders,
        coalesce(t.support_tickets, 0)                                AS support_tickets,
        coalesce(t.open_tickets, 0)                                   AS open_tickets,
        t.avg_csat,
        coalesce(w.web_sessions, 0)                                   AS web_sessions,
        coalesce(w.web_sessions_recent, 0)                            AS web_sessions_recent,
        coalesce(w.abandoned_carts, 0)                                AS abandoned_carts,
        least(c.registered_on, o.first_order_date, CAST(w.first_session_at AS DATE)) AS first_seen_date,
        greatest(o.last_order_date, CAST(t.last_ticket_at AS DATE), CAST(w.last_session_at AS DATE)) AS last_activity_date,
        r.as_of_date, r.active_window_days, r.churn_risk_high_days, r.churn_risk_medium_days,
        r.tier_platinum_min, r.tier_gold_min, r.tier_silver_min
    FROM silver.customers c
    JOIN r USING (tenant_id)
    LEFT JOIN orders o USING (tenant_id, customer_id)
    LEFT JOIN fav f    USING (tenant_id, customer_id)
    LEFT JOIN tix t    USING (tenant_id, customer_id)
    LEFT JOIN web w    USING (tenant_id, customer_id)
), scored AS (
    -- RFM quintiles are ranked among buyers only (5 = best).
    SELECT *,
        CASE WHEN orders > 0 THEN ntile(5) OVER (PARTITION BY tenant_id, orders > 0 ORDER BY recency_days DESC) END AS r_score,
        CASE WHEN orders > 0 THEN ntile(5) OVER (PARTITION BY tenant_id, orders > 0 ORDER BY orders, lifetime_revenue) END AS f_score,
        CASE WHEN orders > 0 THEN ntile(5) OVER (PARTITION BY tenant_id, orders > 0 ORDER BY lifetime_revenue) END AS m_score
    FROM base
)
SELECT
    tenant_id, customer_id, full_name, email, phone, city, region, lead_source, accepts_marketing,
    sources, source_records, account_records, match_rule,
    first_seen_date, registered_on, first_order_date, last_order_date, last_activity_date,
    orders, refunded_orders, lifetime_revenue, avg_order_value, avg_days_between_orders,
    preferred_channel, channels_used, favourite_category,
    support_tickets, open_tickets, avg_csat, web_sessions, web_sessions_recent, abandoned_carts,
    recency_days, r_score, f_score, m_score,
    CASE WHEN orders > 0 THEN CAST(r_score AS VARCHAR) || f_score || m_score END AS rfm_code,
    CASE
        WHEN orders = 0                                    THEN 'Prospect'
        WHEN orders = 1 AND recency_days <= 60             THEN 'New Customer'
        WHEN r_score >= 4 AND f_score >= 4                 THEN 'Champion'
        WHEN r_score >= 3 AND f_score >= 4                 THEN 'Loyal'
        WHEN r_score >= 4 AND f_score >= 2                 THEN 'Potential Loyalist'
        WHEN r_score >= 4                                  THEN 'Promising'
        WHEN r_score = 3                                   THEN 'Needs Attention'
        WHEN r_score = 1 AND f_score >= 4 AND m_score >= 4 THEN 'Cannot Lose Them'
        WHEN r_score <= 2 AND f_score >= 3                 THEN 'At Risk'
        WHEN r_score = 2                                   THEN 'Hibernating'
        ELSE 'Lost'
    END AS rfm_segment,
    CASE
        WHEN orders = 0                            THEN 'Prospect'
        WHEN lifetime_revenue >= tier_platinum_min THEN 'Platinum'
        WHEN lifetime_revenue >= tier_gold_min     THEN 'Gold'
        WHEN lifetime_revenue >= tier_silver_min   THEN 'Silver'
        ELSE 'Bronze'
    END AS tier,
    CASE
        WHEN orders = 0                         THEN 'Prospect'
        WHEN orders = 1 AND recency_days <= 60  THEN 'New'
        WHEN recency_days <= active_window_days THEN 'Active'
        WHEN recency_days <= churn_risk_high_days THEN 'Lapsing'
        ELSE 'Churned'
    END AS lifecycle_stage,
    CASE
        WHEN orders = 0 THEN NULL
        WHEN recency_days > churn_risk_high_days
          OR (avg_days_between_orders IS NOT NULL AND recency_days > 3 * avg_days_between_orders
              AND recency_days > churn_risk_medium_days) THEN 'High'
        WHEN recency_days > churn_risk_medium_days
          OR (avg_days_between_orders IS NOT NULL AND recency_days > 2 * avg_days_between_orders) THEN 'Medium'
        ELSE 'Low'
    END AS churn_risk,
    CASE WHEN orders > 0 THEN
        round(lifetime_revenue / greatest(date_diff('day', first_order_date, as_of_date) / 365.0, 0.25), 2)
    END AS annualized_value
FROM scored;
