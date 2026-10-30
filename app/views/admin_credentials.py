import os
import re

import streamlit as st

from auth import admin_user, require_admin
from cdp import config_store, secrets
from cdp.config import load_tenants

st.markdown("## Credentials & change history")
st.caption("Connection strings, passwords and API tokens are stored encrypted. Values can be replaced but are "
           "never displayed again.")
require_admin()

if secrets.using_dev_key():
    st.warning("CDP_SECRET_KEY is not set, so a development encryption key on this machine is being used. In "
               "production set CDP_SECRET_KEY on the server (generate one with `python -m cdp secret-key`) and keep "
               "it safe: credentials cannot be decrypted without it.", icon=":material/key:")

tab_creds, tab_history = st.tabs([":material/key: Credentials", ":material/history: Change history"])

with tab_creds:
    stored = set(secrets.list_names())
    referenced: dict[str, list[str]] = {}
    for t in load_tenants():
        for s in t["sources"]:
            conn = s["connector"]
            names = [conn["connection_env"]] if conn.get("connection_env") else []
            names += re.findall(r"\$\{(\w+)\}", " ".join([conn.get("url", ""), conn.get("path", ""),
                                                          *conn.get("headers", {}).values()]))
            for n in names:
                referenced.setdefault(n, []).append(f"{t['name']} / {s['name']}")
    rows = []
    for n in sorted(stored | set(referenced)):
        where = "stored (encrypted)" if n in stored else ("environment variable" if os.environ.get(n) else "MISSING")
        rows.append({"name": n, "status": where, "used by": ", ".join(referenced.get(n, [])) or "—"})
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
        missing = [r["name"] for r in rows if r["status"] == "MISSING"]
        if missing:
            st.error("These credentials are used by sources but not set: " + ", ".join(missing))
    else:
        st.info("No credentials yet.")

    with st.form("add_secret", clear_on_submit=True):
        st.markdown("**Add or replace a credential**")
        a, b = st.columns([1, 2])
        name = a.text_input("Name", placeholder="ACME_ERP_DB", help="UPPER_CASE; this is what sources refer to.")
        value = b.text_input("Value", type="password", placeholder="connection string, password or token")
        if st.form_submit_button("Save credential", type="primary", icon=":material/lock:"):
            try:
                secrets.set_secret(name.strip(), value)
                config_store._audit(admin_user(), "save credential", name.strip())
                st.success(f"Saved {name.strip()}.")
                st.rerun()
            except (ValueError, RuntimeError) as exc:
                st.error(str(exc))

    if stored:
        with st.popover("Delete a credential", icon=":material/delete:"):
            victim = st.selectbox("Credential", sorted(stored))
            if referenced.get(victim):
                st.warning("Used by: " + ", ".join(referenced[victim]) + ". Those sources will fail until it is set again.")
            if st.button("Delete", type="primary"):
                secrets.delete_secret(victim)
                config_store._audit(admin_user(), "delete credential", victim)
                st.rerun()

with tab_history:
    log = config_store.audit_log()
    if log:
        st.dataframe(log, hide_index=True, width="stretch")
        st.caption("Every previous version of a changed file is kept in config/.history/.")
    else:
        st.info("No changes recorded yet.")
