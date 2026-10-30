import plotly.express as px
import streamlit as st

from common import (CHANNEL_COLORS, SEGMENT_COLORS, SEGMENT_ORDER, TIER_COLORS, TIER_ORDER, inr, month_start, num,
                    page_header, pct, style_fig, tq)

page_header("Executive overview", "Unified view of customers, revenue and health")

kpi = tq("""
    SELECT count(*)                                              AS profiles,
           count(*) FILTER (WHERE orders > 0)                    AS buyers,
           count(*) FILTER (WHERE lifecycle_stage IN ('Active', 'New')) AS active,
           count(*) FILTER (WHERE orders >= 2) / nullif(count(*) FILTER (WHERE orders > 0), 0) AS repeat_rate,
           sum(lifetime_revenue)                                 AS ltv_total,
           sum(lifetime_revenue) FILTER (WHERE churn_risk = 'High' AND tier IN ('Platinum', 'Gold')) AS value_at_risk,
           avg(avg_csat)                                         AS csat
    FROM gold.customer_360 WHERE tenant_id = ?
""").iloc[0]
# Last 12 months = the as-of month and the 11 before it; compared with the 12 months before that.
start_12m, start_prev_12m = month_start(11), month_start(23)
rev = tq("""
    SELECT sum(net_revenue) FILTER (WHERE month >= $2) AS rev_12m,
           sum(net_revenue) FILTER (WHERE month >= $3 AND month < $2) AS rev_prev_12m,
           sum(net_revenue) FILTER (WHERE month >= $2) / nullif(sum(orders) FILTER (WHERE month >= $2), 0) AS aov_12m
    FROM gold.revenue_monthly WHERE tenant_id = $1
""", start_12m, start_prev_12m).iloc[0]
ident = tq("SELECT count(*) AS records, count(DISTINCT customer_id) AS customers FROM silver.identity_map WHERE tenant_id = ?").iloc[0]

growth = (rev.rev_12m / rev.rev_prev_12m - 1) if rev.rev_prev_12m else None
with st.container(horizontal=True):
    st.metric("Unified customer profiles", num(kpi.profiles), f"from {num(ident.records)} source records",
              delta_color="off", border=True)
    st.metric("Net revenue (last 12 months)", inr(rev.rev_12m), pct(growth) + " vs prior 12m" if growth is not None else None,
              border=True)
    st.metric("Active customers", num(kpi.active), f"{pct(kpi.active / kpi.buyers)} of buyers", delta_color="off",
              border=True)
    st.metric("Repeat purchase rate", pct(kpi.repeat_rate), border=True)
    st.metric("Avg order value (12m)", inr(rev.aov_12m), border=True)
    st.metric("High-value revenue at risk", inr(kpi.value_at_risk), "Gold/Platinum with high churn risk",
              delta_color="off", border=True)

left, right = st.columns([3, 2])
with left, st.container(border=True):
    monthly = tq("""
        SELECT month, channel, sum(net_revenue) AS net_revenue FROM gold.revenue_monthly
        WHERE tenant_id = ? GROUP BY ALL ORDER BY month
    """)
    fig = px.area(monthly, x="month", y="net_revenue", color="channel", color_discrete_map=CHANNEL_COLORS,
                  title="Net revenue by channel", labels={"net_revenue": "Net revenue (₹)", "month": ""})
    st.plotly_chart(style_fig(fig), width="stretch")
with right, st.container(border=True):
    seg = tq("SELECT rfm_segment, sum(customers) AS customers FROM gold.segment_summary WHERE tenant_id = ? "
             "AND rfm_segment <> 'Prospect' GROUP BY 1")
    fig = px.pie(seg, names="rfm_segment", values="customers", hole=0.55, color="rfm_segment",
                 color_discrete_map=SEGMENT_COLORS, title="Buyers by RFM segment",
                 category_orders={"rfm_segment": SEGMENT_ORDER})
    fig.update_traces(textinfo="percent", textposition="inside")
    st.plotly_chart(style_fig(fig, legend=True), width="stretch")

left, mid, right = st.columns(3)
with left, st.container(border=True):
    nr = tq("""
        SELECT month, customer_type, sum(net_revenue) AS net_revenue FROM gold.revenue_monthly
        WHERE tenant_id = ? AND customer_type <> 'Unidentified' AND month >= ? GROUP BY ALL ORDER BY month
    """, month_start(17))
    fig = px.bar(nr, x="month", y="net_revenue", color="customer_type", title="New vs returning revenue (18 months)",
                 color_discrete_map={"New": "#8fb5f9", "Returning": "#1b7f5b"}, labels={"net_revenue": "", "month": ""})
    st.plotly_chart(style_fig(fig, 300), width="stretch")
with mid, st.container(border=True):
    tiers = tq("SELECT tier, count(*) AS customers, sum(lifetime_revenue) AS revenue FROM gold.customer_360 "
               "WHERE tenant_id = ? AND tier <> 'Prospect' GROUP BY 1")
    fig = px.bar(tiers, x="tier", y="revenue", color="tier", color_discrete_map=TIER_COLORS, text="customers",
                 title="Lifetime revenue by tier (label = customers)", category_orders={"tier": TIER_ORDER},
                 labels={"revenue": "", "tier": ""})
    st.plotly_chart(style_fig(fig, 300, legend=False), width="stretch")
with right, st.container(border=True):
    life = tq("SELECT lifecycle_stage, count(*) AS customers FROM gold.customer_360 WHERE tenant_id = ? GROUP BY 1")
    fig = px.bar(life, x="lifecycle_stage", y="customers", color="lifecycle_stage", text="customers",
                 title="Customers by lifecycle stage", labels={"lifecycle_stage": "", "customers": ""},
                 category_orders={"lifecycle_stage": ["Prospect", "New", "Active", "Lapsing", "Churned"]},
                 color_discrete_map={"Prospect": "#c9ced6", "New": "#4c8bf5", "Active": "#2fa37a",
                                     "Lapsing": "#f2b134", "Churned": "#8a8f98"})
    st.plotly_chart(style_fig(fig, 300, legend=False), width="stretch")

with st.container(border=True):
    st.markdown("**Who needs attention this week**")
    attention = tq("""
        SELECT full_name, city, tier, rfm_segment, churn_risk, recency_days, orders, lifetime_revenue, open_tickets, email
        FROM gold.customer_360
        WHERE tenant_id = ? AND tier IN ('Platinum', 'Gold') AND (churn_risk IN ('High', 'Medium') OR open_tickets > 0)
        ORDER BY lifetime_revenue DESC LIMIT 15
    """)
    st.dataframe(attention, hide_index=True, width="stretch", column_config={
        "full_name": "Customer", "rfm_segment": "Segment", "churn_risk": "Churn risk",
        "recency_days": st.column_config.NumberColumn("Days since order"),
        "lifetime_revenue": st.column_config.NumberColumn("Lifetime revenue", format="₹%d"),
        "open_tickets": "Open tickets",
    })
st.caption(f"Average support CSAT: {kpi.csat:.2f} / 5 · Total lifetime revenue: {inr(kpi.ltv_total)}")
