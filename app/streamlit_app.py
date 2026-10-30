"""Customer data platform app (brand from config/platform.json). Run: streamlit run app/streamlit_app.py"""
import streamlit as st

from common import BRAND, tenants

st.set_page_config(page_title=BRAND["product_name"], page_icon=":material/hub:", layout="wide")

all_tenants = tenants()
with st.sidebar:
    st.markdown(f"### :material/hub: {BRAND['product_name']}")
    st.caption(f"{BRAND['tagline']} · by {BRAND['company']}")
    st.selectbox(
        "Business unit",
        options=list(all_tenants),
        format_func=lambda t: all_tenants[t]["name"],
        key="tenant_id",
        help="Each business unit's data and settings are kept separate (multi-tenant).",
    )

pages = {
    "Overview": [
        st.Page("views/overview.py", title="Executive overview", icon=":material/dashboard:", default=True),
    ],
    "Customers": [
        st.Page("views/customer_360.py", title="Customer 360", icon=":material/person_search:"),
        st.Page("views/segments.py", title="Segments & RFM", icon=":material/groups:"),
    ],
    "Analytics": [
        st.Page("views/retention.py", title="Retention & churn", icon=":material/autorenew:"),
        st.Page("views/funnel.py", title="Conversion funnel", icon=":material/filter_alt:"),
        st.Page("views/products.py", title="Product performance", icon=":material/inventory_2:"),
    ],
    "AI": [
        st.Page("views/ask.py", title="Ask your data", icon=":material/auto_awesome:"),
    ],
    "Platform": [
        st.Page("views/pipeline.py", title="Data pipeline", icon=":material/account_tree:"),
    ],
    "Admin": [
        st.Page("views/admin_clients.py", title="Clients", icon=":material/apartment:"),
        st.Page("views/admin_sources.py", title="Sources & connections", icon=":material/cable:"),
        st.Page("views/admin_mappings.py", title="Mappings", icon=":material/schema:"),
        st.Page("views/admin_ai.py", title="AI assistant", icon=":material/smart_toy:"),
        st.Page("views/admin_credentials.py", title="Credentials & history", icon=":material/key:"),
    ],
}
st.navigation(pages).run()
