import os

import streamlit as st

from auth import admin_user, require_admin
from cdp import config_store, llm, secrets
from cdp.config import ConfigError

st.markdown("## AI assistant")
st.caption("Choose which AI service answers questions on the 'Ask your data' page. Use whichever provider your "
           "organisation already has a key for. The key is stored encrypted and never shown again.")
require_admin()

current = llm.load_settings()
providers = list(llm.PROVIDERS)
provider = st.radio("Provider", providers, format_func=lambda p: llm.PROVIDERS[p]["label"],
                    index=providers.index(current["provider"]) if current.get("provider") in providers else 0)
info = llm.PROVIDERS[provider]
same = provider == current.get("provider")
st.caption(info["help"])

with st.container(border=True):
    a, b = st.columns(2)
    model = a.text_input("Deployment name" if provider == "azure_openai" else "Model",
                         value=current.get("model", "") if same else "", key=f"ai_model_{provider}",
                         placeholder=info["default_model"] or ("your deployment name" if provider == "azure_openai"
                                                               else "model name, e.g. from your account's model list"),
                         help="Leave blank to use the default shown." if info["default_model"] else None)
    base_url = ""
    api_version = ""
    if info["needs_base_url"]:
        base_url = b.text_input("Endpoint" if provider == "azure_openai" else "Base URL",
                                value=current.get("base_url", "") if same else "", key=f"ai_url_{provider}",
                                placeholder="https://<resource>.openai.azure.com" if provider == "azure_openai"
                                else "https://api.groq.com/openai/v1")
        if provider == "azure_openai":
            api_version = b.text_input("API version", value=current.get("api_version") or llm.AZURE_API_VERSION,
                                       key="ai_azure_version")

    key_name = info["key"]
    try:
        stored = key_name in secrets.list_names()
    except RuntimeError:
        stored = False
    if stored:
        key_status = "stored (encrypted)"
    elif os.environ.get(key_name):
        key_status = "set as an environment variable"
    else:
        key_status = "not set" + (" (optional for local servers)" if provider == "openai_compatible" else "")
    st.markdown(f"**API key** · credential `{key_name}` · {key_status}")
    new_key = st.text_input("New API key (leave blank to keep the current one)", type="password",
                            key=f"ai_key_{provider}")

    share = st.toggle("Allow sending result rows to the AI provider for summaries",
                      value=bool(current.get("share_result_rows", True)),
                      help="Off: the AI only sees table descriptions and the question; answers have no written "
                           "summary. On: up to 30 result rows are sent to write a short summary.")

draft = {"provider": provider, "model": model.strip(), "base_url": base_url.strip(),
         "api_version": api_version.strip() or None, "share_result_rows": share}


def store_key() -> bool:
    if not new_key.strip():
        return True
    try:
        secrets.set_secret(key_name, new_key.strip())
        config_store._audit(admin_user(), "save credential", key_name)
        return True
    except (ValueError, RuntimeError) as exc:
        st.error(f"Key not saved: {exc}")
        return False


c1, c2 = st.columns([1, 1])
if c1.button("Save", type="primary", icon=":material/save:"):
    try:
        config_store.save_ai_settings(draft, admin_user())
        if store_key():
            st.success(f"Saved. 'Ask your data' now uses {llm.label(draft)}.")
    except ConfigError as exc:
        st.error("Not saved:\n\n- " + "\n- ".join(exc.problems))

if c2.button("Test connection", icon=":material/network_check:"):
    if store_key():
        with st.spinner("Sending a test request…"):
            try:
                llm.test_connection(draft)
                st.success(f"{llm.label(draft)} answered correctly. Click Save to use it, if you haven't already.")
            except Exception as exc:  # show any provider / network error to the admin
                st.error(f"Test failed: {exc}")

st.divider()
st.markdown(f"**In use now:** {llm.label(current)}" + ("" if llm.is_configured(current) else " (incomplete: key, "
                                                         "model or URL missing)"))
st.caption("Tip: keys are credentials like any other; they can also be managed under Admin > Credentials & history, "
           "or set as environment variables of the same name on the server.")
