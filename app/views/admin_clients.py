import streamlit as st

from auth import admin_user, require_admin
from cdp import config_store
from cdp.config import ConfigError, load_tenants, platform

st.markdown("## Clients")
st.caption("Each client (business unit) has its own sources, business rules and data. Changes apply on the next "
           "pipeline run.")
require_admin()

defaults = platform()["default_business_rules"]
tenants = {t["tenant_id"]: t for t in load_tenants()}
NEW = "__new__"
choice = st.selectbox("Client", [NEW, *tenants], index=1 if tenants else 0,
                      format_func=lambda t: "➕ New client" if t == NEW else f"{tenants[t]['name']} ({t})")
current = tenants.get(choice, {"sources": [], "business_rules": {}})
rules = {**defaults, **current.get("business_rules", {}),
         "tier_thresholds": {**defaults["tier_thresholds"], **current.get("business_rules", {}).get("tier_thresholds", {})}}

with st.container(border=True):
    st.markdown("**Details**")
    a, b = st.columns(2)
    tenant_id = a.text_input("Client id", value=current.get("tenant_id", ""), disabled=choice != NEW,
                             help="Permanent, lowercase, e.g. acme_retail. Used in file paths and the database.",
                             key=f"cid_{choice}")
    name = b.text_input("Display name", value=current.get("name", ""), key=f"cname_{choice}")
    c, d, e = st.columns(3)
    industry = c.text_input("Industry", value=current.get("industry", ""), key=f"cind_{choice}")
    currency = d.text_input("Currency", value=current.get("currency", "INR"), max_chars=3, key=f"ccur_{choice}")
    phone_digits = e.number_input("Phone digits to match on", 6, 15, int(current.get("phone_digits", 10)),
                                  help="Phone numbers are matched on their last N digits (10 for India).",
                                  key=f"cph_{choice}")

with st.container(border=True):
    st.markdown("**Business rules**")
    st.caption("Values that differ from the platform defaults are saved for this client only.")
    a, b, c, d = st.columns(4)
    active = a.number_input("Active if ordered within (days)", 1, 3650, int(rules["active_window_days"]),
                            key=f"r1_{choice}")
    churn_med = b.number_input("Churn risk medium after (days)", 1, 3650, int(rules["churn_risk_medium_days"]),
                               key=f"r2_{choice}")
    churn_high = c.number_input("Churn risk high after (days)", 1, 3650, int(rules["churn_risk_high_days"]),
                                key=f"r3_{choice}")
    recent = d.number_input("Recent activity window (days)", 1, 3650, int(rules["recent_activity_days"]),
                            key=f"r4_{choice}")
    a, b, c = st.columns(3)
    tiers = rules["tier_thresholds"]
    plat = a.number_input("Platinum from lifetime spend", 0, 10**9, int(tiers["Platinum"]), step=1000, key=f"t1_{choice}")
    gold = b.number_input("Gold from", 0, 10**9, int(tiers["Gold"]), step=1000, key=f"t2_{choice}")
    silver = c.number_input("Silver from", 0, 10**9, int(tiers["Silver"]), step=1000, key=f"t3_{choice}")

problems = []
if not (silver < gold < plat):
    problems.append("Tier thresholds must increase: Silver < Gold < Platinum.")
if churn_med >= churn_high:
    problems.append("'Churn risk medium' must be fewer days than 'high'.")
for p in problems:
    st.warning(p)

overrides = {}
for key, value in {"active_window_days": active, "churn_risk_medium_days": churn_med,
                   "churn_risk_high_days": churn_high, "recent_activity_days": recent}.items():
    if value != defaults[key]:
        overrides[key] = int(value)
tier_over = {k: int(v) for k, v in {"Platinum": plat, "Gold": gold, "Silver": silver}.items()
             if v != defaults["tier_thresholds"][k]}
if tier_over:
    overrides["tier_thresholds"] = tier_over

save_col, delete_col = st.columns([1, 1])
if save_col.button("Save client", type="primary", icon=":material/save:", disabled=bool(problems)):
    tenant = {**current, "tenant_id": tenant_id.strip(), "name": name.strip(), "industry": industry.strip(),
              "currency": currency.strip().upper(), "phone_digits": int(phone_digits), "business_rules": overrides,
              "sources": current.get("sources", [])}
    if choice == NEW and tenant["tenant_id"] in tenants:
        st.error(f"A client with id '{tenant['tenant_id']}' already exists.")
    else:
        try:
            config_store.save_tenant(tenant, admin_user())
            st.success(f"Saved {tenant['name']}. Add its sources under **Admin > Sources**, then run the pipeline.")
        except ConfigError as exc:
            st.error("Not saved:\n\n- " + "\n- ".join(exc.problems))

if choice != NEW:
    with delete_col.popover("Delete client", icon=":material/delete:"):
        st.write(f"This removes **{current['name']}** and its {len(current['sources'])} source(s) from the "
                 "configuration. Its data disappears from dashboards after the next run. The previous file is "
                 "kept in config/.history.")
        if st.checkbox("I understand", key=f"del_ok_{choice}") and st.button("Delete permanently", type="primary"):
            config_store.delete_tenant(choice, admin_user())
            st.session_state.pop("tenant_id", None)
            st.rerun()

if choice != NEW:
    st.markdown(f"**Sources** ({len(current['sources'])})")
    st.dataframe([{"name": s["name"], "mapping": s["type"], "connector": s["connector"]["kind"]}
                  for s in current["sources"]], hide_index=True, width="stretch")
