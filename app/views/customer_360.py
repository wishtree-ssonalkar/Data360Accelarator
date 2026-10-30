import pandas as pd
import plotly.express as px
import streamlit as st

from common import CHANNEL_COLORS, inr, num, page_header, style_fig, tq

page_header("Customer 360", "Everything we know about one customer, from every system")

NEXT_BEST_ACTION = {
    "Champion": "Reward: invite to VIP early access, ask for a review or referral.",
    "Loyal": "Upsell: recommend premium items in their favourite category.",
    "Potential Loyalist": "Nurture: membership / loyalty programme offer.",
    "New Customer": "Onboard: welcome series and a second-purchase incentive.",
    "Promising": "Engage: personalised recommendations based on first purchase.",
    "Needs Attention": "Re-engage: limited-time offer before they slip.",
    "At Risk": "Win back: personal outreach plus a strong incentive.",
    "Cannot Lose Them": "Urgent win-back: call from the account team; resolve any open issues.",
    "Hibernating": "Low-cost reactivation email with bestsellers.",
    "Lost": "Suppress from paid campaigns; occasional brand newsletter only.",
    "Prospect": "Convert: first-order discount on products they browsed.",
}

search = st.text_input("Search by name, email, phone or customer id", placeholder="e.g. Riya, riya.sharma@gmail.com, 98765…")
term = f"%{search.strip().lower()}%"
matches = tq("""
    SELECT customer_id, full_name, email, city, tier, lifetime_revenue
    FROM gold.customer_360
    WHERE tenant_id = ? AND (? = '%%' OR lower(full_name) LIKE ? OR lower(email) LIKE ? OR phone LIKE ? OR lower(customer_id) LIKE ?)
    ORDER BY lifetime_revenue DESC LIMIT 200
""", term, term, term, term.replace(" ", ""), term)

if matches.empty:
    st.info("No customer matches that search.")
    st.stop()

labels = {r.customer_id: f"{r.full_name} · {r.email or 'no email'} · {r.city or ''} · {r.tier}" for r in matches.itertuples()}
picked = st.selectbox(f"{len(matches)} match{'es' if len(matches) != 1 else ''}" + (" (top 200 by revenue)" if len(matches) == 200 else ""),
                      list(labels), format_func=labels.get)

c = tq("SELECT * FROM gold.customer_360 WHERE tenant_id = ? AND customer_id = ?", picked).iloc[0]

with st.container(border=True):
    head, badges = st.columns([2, 3])
    with head:
        st.markdown(f"### {c.full_name}")
        st.caption(f"{c.customer_id} · {c.city or 'Unknown city'}" + (f" · {c.region} region" if c.region else ""))
        st.markdown(f":material/mail: {c.email or '–'}  \n:material/call: {c.phone or '–'}")
    with badges:
        risk_color = {"High": "red", "Medium": "orange", "Low": "green"}.get(c.churn_risk, "gray")
        st.markdown(
            f":blue-badge[{c.tier}] :violet-badge[{c.rfm_segment}] :gray-badge[{c.lifecycle_stage}] "
            f":{risk_color}-badge[Churn risk: {c.churn_risk or 'n/a'}] "
            + (":green-badge[Marketing opt-in]" if c.accepts_marketing else ":gray-badge[No marketing consent]"))
        st.markdown(f"**Seen in:** {c.sources}  ·  **Matched on:** {c.match_rule}  ·  **Source records:** {c.source_records}")
        st.info(f"**Next best action:** {NEXT_BEST_ACTION.get(c.rfm_segment, '')}", icon=":material/lightbulb:")

with st.container(horizontal=True):
    st.metric("Lifetime revenue", inr(c.lifetime_revenue), border=True)
    st.metric("Orders", num(c.orders), f"{num(c.refunded_orders)} refunded", delta_color="off", border=True)
    st.metric("Avg order value", inr(c.avg_order_value), border=True)
    st.metric("Days since last order", num(c.recency_days), border=True)
    st.metric("RFM score", c.rfm_code or "–", "R · F · M (5 = best)", delta_color="off", border=True)
    st.metric("Support tickets", num(c.support_tickets), f"{num(c.open_tickets)} open", delta_color="off", border=True)
    st.metric("Web sessions (90d)", num(c.web_sessions_recent), f"{num(c.abandoned_carts)} abandoned carts",
              delta_color="off", border=True)

orders = tq("""
    SELECT order_date, order_id, channel, status, items, order_value, store_code
    FROM silver.transactions WHERE tenant_id = ? AND customer_id = ? ORDER BY order_ts DESC
""", picked)
lines = tq("""
    SELECT order_date, order_id, product_name, category, quantity, line_amount, status
    FROM silver.transaction_lines WHERE tenant_id = ? AND customer_id = ? ORDER BY order_date DESC
""", picked)
tickets = tq("""
    SELECT created_at, ticket_id, issue_type, status, priority, csat, sku, channel
    FROM silver.support_tickets WHERE tenant_id = ? AND customer_id = ? ORDER BY created_at DESC
""", picked)
sessions = tq("""
    SELECT started_at, device, events, viewed_product, added_to_cart, started_checkout, purchased
    FROM silver.web_sessions WHERE tenant_id = ? AND customer_id = ? ORDER BY started_at DESC
""", picked)
sources = tq("SELECT source, source_key, email_norm AS email, phone_norm AS phone FROM silver.identity_map "
             "WHERE tenant_id = ? AND customer_id = ? ORDER BY source", picked)

tab_tl, tab_orders, tab_support, tab_web, tab_ids = st.tabs(
    [":material/timeline: Timeline", f":material/shopping_bag: Orders ({len(orders)})",
     f":material/support_agent: Support ({len(tickets)})", f":material/language: Web & app ({len(sessions)})",
     f":material/fingerprint: Source records ({len(sources)})"])

with tab_tl:
    events = pd.concat([
        pd.DataFrame({"when": pd.to_datetime(orders["order_date"]), "type": "Order",
                      "detail": orders["channel"] + " order " + orders["order_id"] + " · " + orders["status"]
                      + " · ₹" + orders["order_value"].round(0).astype(int).astype(str)}),
        pd.DataFrame({"when": pd.to_datetime(tickets["created_at"]), "type": "Support",
                      "detail": tickets["issue_type"] + " · " + tickets["status"]
                      + tickets["csat"].map(lambda v: f" · CSAT {int(v)}" if pd.notna(v) else "")}),
        pd.DataFrame({"when": pd.to_datetime(sessions["started_at"]), "type": "Web session",
                      "detail": sessions["device"] + sessions.apply(
                          lambda s: " · purchased" if s.purchased else (" · abandoned cart" if s.added_to_cart else
                                                                         (" · browsed products" if s.viewed_product else " · visit")),
                          axis=1)}),
    ], ignore_index=True).sort_values("when", ascending=False)
    if len(orders):
        fig = px.scatter(orders, x="order_date", y="order_value", color="channel", size="items", size_max=14,
                         color_discrete_map=CHANNEL_COLORS, title="Purchase history",
                         labels={"order_value": "Order value (₹)", "order_date": ""}, hover_data=["order_id", "status"])
        st.plotly_chart(style_fig(fig, 260), width="stretch")
    st.dataframe(events.head(100), hide_index=True, width="stretch",
                 column_config={"when": st.column_config.DatetimeColumn("When", format="DD MMM YYYY"),
                                "type": "Touchpoint", "detail": "Detail"})

with tab_orders:
    if len(lines):
        cat = lines[lines["status"] == "completed"].groupby("category", as_index=False)["line_amount"].sum()
        a, b = st.columns([1, 2])
        with a:
            fig = px.pie(cat, names="category", values="line_amount", hole=0.5, title="Spend by category")
            st.plotly_chart(style_fig(fig, 280), width="stretch")
        with b:
            st.dataframe(lines, hide_index=True, width="stretch", height=300, column_config={
                "line_amount": st.column_config.NumberColumn("Amount", format="₹%.0f")})
    else:
        st.info("No orders yet; this profile is a prospect.")

with tab_support:
    if len(tickets):
        st.dataframe(tickets, hide_index=True, width="stretch")
    else:
        st.info("No support tickets.")

with tab_web:
    if len(sessions):
        st.dataframe(sessions.head(200), hide_index=True, width="stretch")
    else:
        st.info("No identified web or app sessions.")

with tab_ids:
    st.caption("These are the raw records from each system that identity resolution merged into this one profile. "
               "Records are linked when they share a normalized email or phone number.")
    st.dataframe(sources, hide_index=True, width="stretch")
