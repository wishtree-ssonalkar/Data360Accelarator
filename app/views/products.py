import plotly.express as px
import streamlit as st

from common import inr, num, page_header, pct, style_fig, tq

page_header("Product performance", "What sells, what earns, and what causes problems")

prod = tq("SELECT * FROM gold.product_performance WHERE tenant_id = ?")
cats = st.multiselect("Category", sorted(prod["category"].unique()))
if cats:
    prod = prod[prod["category"].isin(cats)]

with st.container(horizontal=True):
    st.metric("Net product revenue", inr(prod["net_revenue"].sum()), border=True)
    st.metric("Gross margin", pct(prod["gross_margin"].sum() / prod["net_revenue"].sum()), border=True)
    st.metric("Units sold", num(prod["units_sold"].sum()), border=True)
    st.metric("Avg refund rate", pct((prod["refund_rate"] * prod["orders"]).sum() / prod["orders"].sum()), border=True)
    st.metric("Product-related tickets", num(prod["support_tickets"].sum()), border=True)

left, right = st.columns(2)
with left, st.container(border=True):
    cat = prod.groupby("category", as_index=False)[["net_revenue", "gross_margin"]].sum().sort_values("net_revenue")
    fig = px.bar(cat, x=["net_revenue", "gross_margin"], y="category", orientation="h", barmode="group",
                 title="Revenue and margin by category", labels={"value": "₹", "category": "", "variable": ""},
                 color_discrete_sequence=["#4c8bf5", "#1b7f5b"])
    st.plotly_chart(style_fig(fig, 380), width="stretch")
with right, st.container(border=True):
    top = prod.nlargest(12, "net_revenue").sort_values("net_revenue")
    fig = px.bar(top, x="net_revenue", y="product_name", color="category", orientation="h",
                 title="Top 12 products by net revenue", labels={"net_revenue": "Net revenue (₹)", "product_name": ""})
    st.plotly_chart(style_fig(fig, 380), width="stretch")

with st.container(border=True):
    risky = prod[prod["orders"] >= 10]
    fig = px.scatter(risky, x="refund_rate", y="support_tickets", size="net_revenue", color="category",
                     hover_name="product_name", hover_data={"sku": True, "avg_ticket_csat": True},
                     title="Problem products: refund rate vs support tickets (bubble = revenue)",
                     labels={"refund_rate": "Refund rate", "support_tickets": "Support tickets"})
    fig.update_xaxes(tickformat=".0%")
    st.plotly_chart(style_fig(fig, 420), width="stretch")
    st.caption("Products in the top-right are refunded often and generate complaints: check sizing guides, "
               "photos, packaging or the supplier.")

st.dataframe(prod.sort_values("net_revenue", ascending=False), hide_index=True, width="stretch", column_config={
    "net_revenue": st.column_config.NumberColumn("Net revenue", format="₹%.0f"),
    "gross_margin": st.column_config.NumberColumn("Gross margin", format="₹%.0f"),
    "refunded_amount": st.column_config.NumberColumn("Refunded", format="₹%.0f"),
    "list_price": st.column_config.NumberColumn("List price", format="₹%.0f"),
    "margin_pct": st.column_config.NumberColumn("Margin %", format="percent"),
    "refund_rate": st.column_config.NumberColumn("Refund rate", format="percent"),
})
