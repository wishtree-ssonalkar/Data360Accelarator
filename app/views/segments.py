import plotly.express as px
import streamlit as st

from common import SEGMENT_COLORS, SEGMENT_ORDER, TIER_ORDER, inr, num, page_header, style_fig, tenant, tq

page_header("Segments & RFM", "Recency · Frequency · Monetary scoring and campaign-ready audiences")

with st.expander("How RFM works", icon=":material/help:"):
    st.markdown(
        "Every buyer gets three scores from 1 to 5, ranked against the other buyers of this business:\n"
        "- **R**ecency: how recently they last bought (5 = most recent)\n"
        "- **F**requency: how many orders they have placed (5 = most)\n"
        "- **M**onetary: how much they have spent in total (5 = most)\n\n"
        "The R and F scores place each customer in a named segment (Champion, At Risk, ...) that marketing can act on. "
        "Tiers (Platinum/Gold/Silver/Bronze) use lifetime-revenue thresholds set per business unit in "
        "`config/business_rules.json`.")

seg = tq("""
    SELECT rfm_segment, sum(customers) AS customers, sum(revenue) AS revenue,
           sum(avg_recency_days * customers) / sum(customers) AS avg_recency_days,
           sum(avg_orders * customers) / sum(customers) AS avg_orders,
           sum(marketable_customers) AS marketable
    FROM gold.segment_summary WHERE tenant_id = ? GROUP BY 1
""")
seg["revenue_share"] = seg["revenue"] / seg["revenue"].sum()
seg = seg.set_index("rfm_segment").reindex([s for s in SEGMENT_ORDER if s in set(seg["rfm_segment"])]).reset_index()

left, right = st.columns([3, 2])
with left, st.container(border=True):
    buyers = seg[seg["rfm_segment"] != "Prospect"]
    fig = px.treemap(buyers, path=["rfm_segment"], values="customers", color="rfm_segment",
                     color_discrete_map=SEGMENT_COLORS, title="Segment size (area) · hover for revenue",
                     hover_data={"revenue": ":,.0f"})
    fig.update_traces(texttemplate="%{label}<br>%{value} customers")
    st.plotly_chart(style_fig(fig, 380, legend=False), width="stretch")
with right, st.container(border=True):
    grid = tq("""
        SELECT r_score, f_score, count(*) AS customers, sum(lifetime_revenue) AS revenue
        FROM gold.customer_360 WHERE tenant_id = ? AND orders > 0 GROUP BY ALL
    """)
    heat = grid.pivot(index="f_score", columns="r_score", values="customers").sort_index(ascending=False)
    fig = px.imshow(heat, text_auto=True, aspect="auto", color_continuous_scale="Blues",
                    labels={"x": "Recency score (5 = recent)", "y": "Frequency score", "color": "Customers"},
                    title="RFM grid: customers by R x F")
    st.plotly_chart(style_fig(fig, 380, legend=False), width="stretch")

st.dataframe(seg, hide_index=True, width="stretch", column_config={
    "rfm_segment": "Segment", "customers": st.column_config.NumberColumn("Customers", format="%d"),
    "revenue": st.column_config.NumberColumn("Lifetime revenue", format="₹%.0f"),
    "revenue_share": st.column_config.ProgressColumn("Revenue share", format="percent", min_value=0, max_value=1),
    "avg_recency_days": st.column_config.NumberColumn("Avg days since order", format="%.0f"),
    "avg_orders": st.column_config.NumberColumn("Avg orders", format="%.1f"),
    "marketable": st.column_config.NumberColumn("Opted in to marketing", format="%d"),
})

st.markdown("#### Build an audience")
with st.container(border=True):
    f1, f2, f3, f4 = st.columns(4)
    segs = f1.multiselect("Segment", [s for s in SEGMENT_ORDER if s in set(seg["rfm_segment"])],
                          default=["At Risk", "Cannot Lose Them"])
    tiers = f2.multiselect("Tier", TIER_ORDER, default=[])
    cities = f3.multiselect("City", tq("SELECT DISTINCT city FROM gold.customer_360 WHERE tenant_id = ? AND city IS NOT NULL ORDER BY 1")["city"].tolist())
    only_optin = f4.toggle("Only marketing opt-ins", value=True)

    audience = tq("""
        SELECT customer_id, full_name, email, phone, city, tier, rfm_segment, churn_risk, favourite_category,
               orders, lifetime_revenue, recency_days, accepts_marketing
        FROM gold.customer_360 WHERE tenant_id = ? ORDER BY lifetime_revenue DESC
    """)
    if segs:
        audience = audience[audience["rfm_segment"].isin(segs)]
    if tiers:
        audience = audience[audience["tier"].isin(tiers)]
    if cities:
        audience = audience[audience["city"].isin(cities)]
    if only_optin:
        audience = audience[audience["accepts_marketing"]]

    with st.container(horizontal=True):
        st.metric("Audience size", num(len(audience)), border=True)
        st.metric("Their lifetime revenue", inr(audience["lifetime_revenue"].sum()), border=True)
        st.metric("Top category", audience["favourite_category"].mode().iat[0] if len(audience) else "–", border=True)
    st.dataframe(audience, hide_index=True, width="stretch", height=320, column_config={
        "lifetime_revenue": st.column_config.NumberColumn("Lifetime revenue", format="₹%.0f")})
    st.download_button("Export audience (CSV)", audience.to_csv(index=False).encode("utf-8"),
                       file_name=f"{tenant()['tenant_id']}_audience.csv", mime="text/csv",
                       icon=":material/download:", disabled=audience.empty)
