"""Admin access for configuration pages.

Until SSO is added, admin pages require the password in CDP_ADMIN_PASSWORD. With CDP_ENV=production
(set in the Docker image) the pages stay locked when no password is configured; in development they open
with a warning so the product can be tried locally.
"""
from __future__ import annotations

import hmac
import os
import time

import streamlit as st

_MAX_ATTEMPTS = 5
_LOCKOUT_SECONDS = 300


def _open_dev_mode() -> bool:
    return not os.environ.get("CDP_ADMIN_PASSWORD") and os.environ.get("CDP_ENV", "").lower() != "production"


def is_admin() -> bool:
    return bool(st.session_state.get("_admin")) or _open_dev_mode()


def admin_user() -> str:
    return st.session_state.get("_admin_user", "admin")


def require_admin() -> None:
    """Stop the page unless the viewer is signed in as admin (shows a sign-in form)."""
    if is_admin():
        with st.sidebar:
            if st.button("Sign out of admin", icon=":material/logout:"):
                st.session_state.pop("_admin", None)
                st.rerun()
        return
    password = os.environ.get("CDP_ADMIN_PASSWORD")
    if not password:
        if os.environ.get("CDP_ENV", "").lower() == "production":
            st.error("Admin pages are locked: set the CDP_ADMIN_PASSWORD environment variable on the server.",
                     icon=":material/lock:")
            st.stop()
        st.warning("Development mode: admin pages are open because CDP_ADMIN_PASSWORD is not set. "
                   "Set it (and CDP_ENV=production) before deploying.", icon=":material/lock_open:")
        st.session_state["_admin"] = True
        return

    lock_until = st.session_state.get("_admin_lock_until", 0)
    if time.time() < lock_until:
        st.error(f"Too many attempts. Try again in {int(lock_until - time.time())} seconds.")
        st.stop()
    st.markdown("### :material/admin_panel_settings: Admin sign-in")
    st.caption("Configuration pages (clients, sources, mappings, credentials) need the admin password.")
    with st.form("admin_login"):
        name = st.text_input("Your name (recorded in the change history)")
        entered = st.text_input("Admin password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")
    if submitted:
        if hmac.compare_digest(entered.encode(), password.encode()):
            st.session_state["_admin"] = True
            st.session_state["_admin_user"] = name.strip() or "admin"
            st.session_state["_admin_fails"] = 0
            st.rerun()
        fails = st.session_state.get("_admin_fails", 0) + 1
        st.session_state["_admin_fails"] = fails
        if fails >= _MAX_ATTEMPTS:
            st.session_state["_admin_lock_until"] = time.time() + _LOCKOUT_SECONDS
            st.session_state["_admin_fails"] = 0
        st.error("Wrong password.")
    st.stop()
