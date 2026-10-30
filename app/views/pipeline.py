import os
import subprocess
import sys


import plotly.express as px
import streamlit as st

from auth import is_admin
from cdp.config import LOG_DIR, ROOT
from cdp.pipeline import BUILD_PATH
from common import num, page_header, pct, query, style_fig, tenant_id, tq

RUN_LOG = LOG_DIR / "last_run.out"

page_header("Data pipeline", "Sources, layers, identity resolution, data quality and business rules")

run = query("SELECT * FROM meta.runs WHERE status <> 'running' ORDER BY started_at DESC LIMIT 1").iloc[0]
models = query("SELECT * FROM meta.models ORDER BY layer, name")
sources = tq("SELECT * FROM meta.sources WHERE tenant_id = ? ORDER BY source_name")
latest = tq("""
    SELECT source_name, source_system, connector, load_mode, status, rows_loaded, seconds, error, loaded_at
    FROM meta.source_runs WHERE tenant_id = ?
    QUALIFY row_number() OVER (PARTITION BY source_name ORDER BY loaded_at DESC) = 1
    ORDER BY source_name
""")

with st.container(horizontal=True):
    st.metric("Last pipeline run", f"{run.finished_at:%d %b %Y %H:%M}", run.status, delta_color="off", border=True)
    st.metric("Sources connected", num(len(sources)),
              f"{(latest['status'] == 'failed').sum()} failing", delta_color="off", border=True)
    st.metric("Models built", num(len(models)), border=True)
    st.metric("Data-quality checks passing",
              pct(tq("SELECT avg(CASE WHEN status = 'pass' THEN 1 ELSE 0 END) FROM meta.dq_results "
                     "WHERE tenant_id = ?").iat[0, 0]), border=True)
if run.message:
    st.warning(run.message, icon=":material/warning:")


@st.fragment(run_every="3s")
def run_controls() -> None:
    proc = st.session_state.get("_run_proc")
    running = BUILD_PATH.exists() or (proc is not None and proc.poll() is None)
    a, b = st.columns([1, 3])
    if running:
        a.button("Refresh running…", disabled=True, icon=":material/hourglass_top:")
        log_tail = RUN_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-1:] if RUN_LOG.exists() else []
        b.info(log_tail[0] if log_tail else "Starting…", icon=":material/sync:")
        st.session_state["_run_was_running"] = True
    else:
        if st.session_state.pop("_run_was_running", False):
            st.cache_data.clear()
            st.rerun(scope="app")  # show the new data
        if not is_admin():
            b.caption("Sign in to an Admin page to start a refresh from here.")
        elif a.button("Run now", type="primary", icon=":material/play_arrow:"):
            RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(RUN_LOG, "w", encoding="utf-8") as out:
                st.session_state["_run_proc"] = subprocess.Popen(
                    [sys.executable, "-m", "cdp", "run"], cwd=str(ROOT), stdout=out, stderr=subprocess.STDOUT,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            st.session_state["_run_was_running"] = True
            st.rerun(scope="fragment")


run_controls()

tab_flow, tab_sources, tab_runs, tab_identity, tab_dq, tab_rules = st.tabs([
    ":material/account_tree: Architecture & lineage", ":material/cable: Sources", ":material/history: Run history",
    ":material/fingerprint: Identity resolution", ":material/verified: Data quality", ":material/tune: Business rules"])

with tab_flow:
    source_nodes = " ".join(f'"{s}";' for s in sorted(sources["source_system"].unique()))
    source_edges = " ".join(f'"{s}" -> Connect;' for s in sorted(sources["source_system"].unique()))
    st.graphviz_chart(f"""
    digraph {{
        rankdir=LR; bgcolor="transparent"; node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11];
        subgraph cluster_s {{ label="Sources (this business unit)"; color="#cccccc"; fontname="Helvetica";
            node [fillcolor="#eef2ff"]; {source_nodes} }}
        Connect [label="1. Connect\\nfile / database / API\\nconnectors (bronze)", fillcolor="#fff4d6"];
        Model [label="2. Map to standard model\\ngenerated from config\\n(silver.std_*)", fillcolor="#e3f4ea"];
        Identity [label="Identity resolution\\nemail + phone + id graph", fillcolor="#e3f4ea"];
        C360 [label="3. Customer 360\\nRFM, segments, tiers,\\nchurn risk (gold)", fillcolor="#d7ecff"];
        Analytics [label="4. Analytics\\nretention, funnel,\\nproducts, revenue", fillcolor="#d7ecff"];
        AI [label="5. Ask your data\\nLLM -> SQL -> chart", fillcolor="#f1e6ff"];
        {source_edges}
        Connect -> Model -> Identity -> C360 -> Analytics; C360 -> AI; Analytics -> AI;
    }}""", width="stretch")
    st.markdown("**Model lineage** (built from the `depends_on` header of each SQL file)")
    edges = []
    for m in models.itertuples():
        for dep in [d.strip() for d in (m.depends_on or "").split(",") if d.strip()]:
            edges.append(f'"{dep}" -> "{m.name}";')
    colors = {"bronze": "#fff4d6", "silver": "#e3f4ea", "gold": "#d7ecff", "meta": "#eeeeee"}
    nodes = {n for e in edges for n in e.replace(";", "").replace('"', "").split(" -> ")}
    node_defs = [f'"{n}" [fillcolor="{colors.get(n.split(".")[0], "#ffffff")}"];' for n in nodes]
    st.graphviz_chart("digraph { rankdir=LR; bgcolor=\"transparent\"; node [shape=box, style=\"rounded,filled\", "
                      "fontname=\"Helvetica\", fontsize=10]; " + " ".join(node_defs + edges) + " }", width="stretch")
    st.dataframe(models, hide_index=True, width="stretch", column_config={
        "row_count": st.column_config.NumberColumn("Rows", format="%d"), "seconds": "Build seconds"})

with tab_sources:
    st.caption(f"Sources are declared per business unit in `config/tenants/{tenant_id()}.json`; how each kind of "
               "system maps to the standard model lives in `config/source_types/`. Adding a source is configuration, "
               "not code.")
    st.dataframe(sources.merge(latest[["source_name", "status", "rows_loaded", "loaded_at", "error"]],
                               on="source_name", how="left"),
                 hide_index=True, width="stretch", column_config={
                     "tenant_id": None,
                     "rows_loaded": st.column_config.NumberColumn("Rows (last load)", format="%d"),
                     "priority": st.column_config.NumberColumn("Golden-record priority", help="Lower wins"),
                     "loaded_at": st.column_config.DatetimeColumn("Last load", format="DD MMM HH:mm")})

with tab_runs:
    st.dataframe(query("SELECT run_id, started_at, finished_at, status, tenants, message FROM meta.runs "
                       "ORDER BY started_at DESC LIMIT 50"), hide_index=True, width="stretch")
    st.markdown("**Per-source results**")
    st.dataframe(tq("SELECT run_id, source_name, connector, load_mode, status, rows_loaded, seconds, error "
                    "FROM meta.source_runs WHERE tenant_id = ? ORDER BY loaded_at DESC LIMIT 200"),
                 hide_index=True, width="stretch")

with tab_identity:
    ids = tq("""
        SELECT source, count(*) AS records, count(DISTINCT customer_id) AS customers
        FROM silver.identity_map WHERE tenant_id = ? GROUP BY 1 ORDER BY 2 DESC
    """)
    total = tq("SELECT count(*) AS records, count(DISTINCT customer_id) AS customers FROM silver.identity_map WHERE tenant_id = ?").iloc[0]
    rules = tq("""
        SELECT match_rule, count(*) AS customers FROM silver.customers WHERE tenant_id = ? GROUP BY 1 ORDER BY 2 DESC
    """)
    multi = tq("SELECT source_records, count(*) AS customers FROM silver.customers WHERE tenant_id = ? GROUP BY 1 ORDER BY 1")
    with st.container(horizontal=True):
        st.metric("Source records with an identifier", num(total.records), border=True)
        st.metric("Unified customer profiles", num(total.customers), border=True)
        st.metric("Duplicate records removed", num(total.records - total.customers),
                  pct(1 - total.customers / total.records), delta_color="off", border=True)
    a, b = st.columns(2)
    with a:
        fig = px.bar(ids, x="source", y="records", title="Identity records by source", text="records")
        st.plotly_chart(style_fig(fig, 320, legend=False), width="stretch")
    with b:
        fig = px.bar(multi, x="source_records", y="customers", title="Source records merged per profile",
                     labels={"source_records": "Records per profile"})
        st.plotly_chart(style_fig(fig, 320, legend=False), width="stretch")
    st.markdown("**How profiles were matched**")
    st.dataframe(rules, hide_index=True, width="stretch")
    st.caption("Matching is deterministic: records that share a source customer id, a normalized email "
               "(lower-cased, trimmed) or a normalized phone (last N digits, set per business unit) are linked, and "
               "each connected group is one customer.")

with tab_dq:
    dq = tq("SELECT check_name, table_name, value, threshold, status FROM meta.dq_results WHERE tenant_id = ?")
    st.dataframe(dq, hide_index=True, width="stretch", column_config={
        "value": st.column_config.ProgressColumn("Result", format="percent", min_value=0, max_value=1),
        "threshold": st.column_config.NumberColumn("Target", format="percent"),
        "status": st.column_config.TextColumn("Status")})

with tab_rules:
    st.caption("Defaults live in `config/platform.json`; each business unit can override them in the "
               f"`business_rules` block of `config/tenants/{tenant_id()}.json`. Re-run the pipeline to apply.")
    rules = query("SELECT * FROM meta.business_rules WHERE tenant_id = ?", (tenant_id(),))
    st.dataframe(rules.T.rename(columns={0: "value"}).astype(str), width="stretch")

st.code("python -m cdp validate              # check configuration\n"
        "python -m cdp run                   # ingest all sources and rebuild everything\n"
        "python -m cdp run --tenant stride   # ingest one business unit\n"
        "python -m cdp status                # last runs and per-source results", language="bash")
