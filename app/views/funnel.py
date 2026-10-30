import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from common import num, page_header, pct, style_fig, tq

page_header("Conversion funnel", "From visit to purchase across web and app")

raw = tq("SELECT * FROM gold.funnel_monthly WHERE tenant_id = ? ORDER BY month")
months = sorted(raw["month"].astype(str).str[:7].unique())

c1, c2 = st.columns([3, 1])
start, end = c1.select_slider("Period", options=months, value=(months[-12], months[-1]))
devices = c2.multiselect("Device", sorted(raw["device"].unique()), default=sorted(raw["device"].unique()))

df = raw[(raw["month"].astype(str).str[:7].between(start, end)) & raw["device"].isin(devices)]
steps = ["sessions", "product_views", "add_to_carts", "checkouts", "purchases"]
labels = ["Visited", "Viewed a product", "Added to cart", "Started checkout", "Purchased"]
totals = df[steps].sum()

with st.container(horizontal=True):
    st.metric("Sessions", num(totals.sessions), border=True)
    st.metric("Conversion rate", pct(totals.purchases / totals.sessions if totals.sessions else None), border=True)
    st.metric("Cart abandonment", pct(1 - totals.purchases / totals.add_to_carts if totals.add_to_carts else None),
              border=True)
    st.metric("Checkout completion", pct(totals.purchases / totals.checkouts if totals.checkouts else None), border=True)
    st.metric("Identified sessions", pct(df["identified_sessions"].sum() / totals.sessions if totals.sessions else None),
              "linked to a customer profile", delta_color="off", border=True)

left, right = st.columns([2, 3])
with left, st.container(border=True):
    fig = go.Figure(go.Funnel(y=labels, x=[totals[s] for s in steps], textinfo="value+percent initial",
                              marker={"color": ["#c9d8fb", "#9dbaf8", "#6f9bf6", "#4c8bf5", "#1b7f5b"]}))
    fig.update_layout(title="Funnel for selected period")
    st.plotly_chart(style_fig(fig, 380, legend=False), width="stretch")
with right, st.container(border=True):
    trend = df.groupby(["month", "device"], as_index=False)[["sessions", "purchases"]].sum()
    trend["conversion_rate"] = trend["purchases"] / trend["sessions"]
    fig = px.line(trend, x="month", y="conversion_rate", color="device", markers=True,
                  title="Conversion rate by device", labels={"conversion_rate": "", "month": ""})
    fig.update_yaxes(tickformat=".1%")
    st.plotly_chart(style_fig(fig, 380), width="stretch")

by_device = df.groupby("device", as_index=False)[steps].sum()
for a, b, name in [("sessions", "product_views", "view_rate"), ("product_views", "add_to_carts", "cart_rate"),
                   ("add_to_carts", "checkouts", "checkout_rate"), ("checkouts", "purchases", "purchase_rate")]:
    by_device[name] = by_device[b] / by_device[a]
st.dataframe(by_device, hide_index=True, width="stretch", column_config={
    **{k: st.column_config.NumberColumn(format="percent") for k in ["view_rate", "cart_rate", "checkout_rate", "purchase_rate"]},
})
