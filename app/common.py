"""Shared helpers for the Streamlit app: warehouse access, tenant context, formatting."""
from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cdp.config import AS_OF_DATE, WAREHOUSE_PATH, brand, load_tenants  # noqa: E402

BRAND = brand()

SEGMENT_ORDER = ["Champion", "Loyal", "Potential Loyalist", "New Customer", "Promising", "Needs Attention",
                 "At Risk", "Cannot Lose Them", "Hibernating", "Lost", "Prospect"]
SEGMENT_COLORS = {
    "Champion": "#1b7f5b", "Loyal": "#2fa37a", "Potential Loyalist": "#6cc49a", "New Customer": "#4c8bf5",
    "Promising": "#8fb5f9", "Needs Attention": "#f2b134", "At Risk": "#ee7b30", "Cannot Lose Them": "#d6453d",
    "Hibernating": "#a58bd6", "Lost": "#8a8f98", "Prospect": "#c9ced6",
}
TIER_ORDER = ["Platinum", "Gold", "Silver", "Bronze", "Prospect"]
TIER_COLORS = {"Platinum": "#5b6cff", "Gold": "#e0a526", "Silver": "#9aa4b2", "Bronze": "#b8763e", "Prospect": "#d5d9e0"}
CHANNEL_COLORS = {"Web": "#4c8bf5", "App": "#7c5cf0", "Store": "#2fa37a"}
RISK_COLORS = {"High": "#d6453d", "Medium": "#f2b134", "Low": "#2fa37a"}


def warehouse_version() -> float:
    """Changes whenever the pipeline publishes a new build; part of every cache key."""
    if not WAREHOUSE_PATH.exists():
        st.error("The warehouse has not been built yet. Run `python -m cdp run` first.")
        st.stop()
    return WAREHOUSE_PATH.stat().st_mtime


@st.cache_data(ttl=3600, show_spinner=False, max_entries=500)
def _query(sql: str, params: tuple, version: float) -> pd.DataFrame:
    # A short-lived read-only connection per (uncached) query: the file is never held open, so the
    # pipeline can swap in a fresh build at any time.
    for attempt in range(5):
        try:
            con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
            break
        except duckdb.IOException:
            if attempt == 4:
                raise
            time.sleep(0.5)
    try:
        return con.execute(sql, list(params)).df()
    finally:
        con.close()


def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    return _query(sql, tuple(params), warehouse_version())


def month_start(months_back: int = 0) -> date:
    """First day of the month `months_back` months before the as-of month (0 = the as-of month itself)."""
    index = AS_OF_DATE.year * 12 + AS_OF_DATE.month - 1 - months_back
    return date(index // 12, index % 12 + 1, 1)


def tenants() -> dict[str, dict]:
    return {t["tenant_id"]: t for t in load_tenants()}


def tenant_id() -> str:
    known = tenants()
    chosen = st.session_state.get("tenant_id")
    return chosen if chosen in known else next(iter(known))


def tenant() -> dict:
    return tenants()[tenant_id()]


def tq(sql: str, *params) -> pd.DataFrame:
    """Query scoped to the current tenant. Use `?` for the tenant id as the first parameter."""
    return query(sql, (tenant_id(), *params))


def inr(value, decimals: int = 1) -> str:
    """Indian-style compact currency: Rs 1.2 Cr, Rs 3.4 L, Rs 12.5 K."""
    if value is None or pd.isna(value):
        return "–"
    v = float(value)
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1e7:
        return f"{sign}₹{v / 1e7:.{decimals}f} Cr"
    if v >= 1e5:
        return f"{sign}₹{v / 1e5:.{decimals}f} L"
    if v >= 1e3:
        return f"{sign}₹{v / 1e3:.{decimals}f} K"
    return f"{sign}₹{v:,.0f}"


def num(value) -> str:
    return "–" if value is None or pd.isna(value) else f"{int(value):,}"


def pct(value, decimals: int = 1) -> str:
    return "–" if value is None or pd.isna(value) else f"{value * 100:.{decimals}f}%"


def style_fig(fig, height: int = 360, legend: bool = True):
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=36, b=8), legend_title_text="",
        showlegend=legend, font=dict(size=12),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        title_font=dict(size=14),
    )
    return fig


def page_header(title: str, subtitle: str) -> None:
    st.markdown(f"## {title}")
    st.caption(f"{subtitle} · **{tenant()['name']}** · data as of {AS_OF_DATE:%d %b %Y}")
    loaded = query("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'gold' "
                   "AND table_name = 'customer_360'").iat[0, 0]
    if not loaded or not query("SELECT count(*) FROM gold.customer_360 WHERE tenant_id = ?", (tenant_id(),)).iat[0, 0]:
        st.info(f"No data for **{tenant()['name']}** yet. Add its sources under **Admin > Sources & connections**, "
                "then start a refresh on **Data pipeline > Run now**.", icon=":material/info:")
        st.stop()
