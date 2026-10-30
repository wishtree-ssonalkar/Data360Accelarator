import plotly.express as px
import streamlit as st

from common import RISK_COLORS, TIER_ORDER, inr, month_start, num, page_header, pct, style_fig, tq

page_header("Retention & churn", "Do customers come back, and who is about to leave?")

cohorts = tq("""
    SELECT cohort_month, months_since_first, cohort_size, retention_rate
    FROM gold.cohort_retention WHERE tenant_id = ? AND cohort_month >= ? ORDER BY 1, 2
""", month_start(23))
stats = tq("""
    SELECT count(*) FILTER (WHERE orders >= 2) / count(*) AS repeat_rate,
           median(avg_days_between_orders) AS median_gap,
           count(*) FILTER (WHERE churn_risk = 'High') AS high_risk,
           sum(lifetime_revenue) FILTER (WHERE churn_risk = 'High') AS high_risk_revenue,
           count(*) FILTER (WHERE lifecycle_stage = 'Churned') / count(*) AS churned_share
    FROM gold.customer_360 WHERE tenant_id = ? AND orders > 0
""").iloc[0]
m3 = cohorts[cohorts["months_since_first"].between(1, 3)].groupby("cohort_month").apply(
    lambda g: g["retention_rate"].max(), include_groups=False).mean()

with st.container(horizontal=True):
    st.metric("Repeat purchase rate", pct(stats.repeat_rate), "buyers with 2+ orders", delta_color="off", border=True)
    st.metric("Median days between orders", num(stats.median_gap), border=True)
    st.metric("Came back within 3 months", pct(m3), "avg across cohorts", delta_color="off", border=True)
    st.metric("High churn-risk customers", num(stats.high_risk), inr(stats.high_risk_revenue) + " lifetime value",
              delta_color="off", border=True)
    st.metric("Churned share of buyers", pct(stats.churned_share), border=True)

with st.container(border=True):
    heat = cohorts[cohorts["months_since_first"] > 0].pivot(
        index="cohort_month", columns="months_since_first", values="retention_rate")
    sizes = cohorts.drop_duplicates("cohort_month").set_index("cohort_month")["cohort_size"]
    heat.index = [f"{str(m)[:7]} (n={sizes[m]})" for m in heat.index]
    fig = px.imshow(heat, text_auto=".0%", aspect="auto", color_continuous_scale="Greens",
                    labels={"x": "Months since first order", "y": "Cohort (first order month)", "color": "Bought again"},
                    title="Cohort retention (last 24 cohorts): share that ordered again N months later")
    fig.update_xaxes(dtick=1)
    st.plotly_chart(style_fig(fig, 520, legend=False), width="stretch")

left, right = st.columns(2)
with left, st.container(border=True):
    risk = tq("""
        SELECT tier, churn_risk, count(*) AS customers FROM gold.customer_360
        WHERE tenant_id = ? AND orders > 0 GROUP BY ALL
    """)
    fig = px.bar(risk, x="tier", y="customers", color="churn_risk", color_discrete_map=RISK_COLORS,
                 category_orders={"tier": TIER_ORDER, "churn_risk": ["High", "Medium", "Low"]},
                 title="Churn risk by tier", labels={"tier": "", "customers": "Customers"})
    st.plotly_chart(style_fig(fig, 340), width="stretch")
with right, st.container(border=True):
    rec = tq("SELECT recency_days FROM gold.customer_360 WHERE tenant_id = ? AND orders > 0")
    fig = px.histogram(rec, x="recency_days", nbins=40, title="Days since last order (all buyers)",
                       labels={"recency_days": "Days since last order"}, color_discrete_sequence=["#4c8bf5"])
    st.plotly_chart(style_fig(fig, 340, legend=False), width="stretch")

st.markdown("#### Win-back list: valuable customers at high risk")
st.dataframe(tq("""
    SELECT full_name, email, city, tier, rfm_segment, recency_days, avg_days_between_orders, orders,
           lifetime_revenue, open_tickets, avg_csat
    FROM gold.customer_360 WHERE tenant_id = ? AND churn_risk = 'High' AND tier IN ('Platinum', 'Gold', 'Silver')
    ORDER BY lifetime_revenue DESC LIMIT 50
"""), hide_index=True, width="stretch", column_config={
    "lifetime_revenue": st.column_config.NumberColumn("Lifetime revenue", format="₹%.0f"),
    "avg_days_between_orders": st.column_config.NumberColumn("Usual gap (days)", format="%.0f"),
    "recency_days": "Days since order"})
