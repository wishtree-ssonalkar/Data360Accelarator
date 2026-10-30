import re

import streamlit as st

from auth import admin_user, require_admin
from cdp import config_store
from cdp.config import SOURCES_DIR, ConfigError, load_source_types, load_tenants
from cdp.connectors import SourceError, preview
from cdp.secrets import list_names
from common import query

st.markdown("## Sources & connections")
st.caption("Connect each client's systems: upload files, point at cloud storage, or connect a database or API. "
           "Test the connection, check the mapped result, then save. Changes apply on the next pipeline run.")
require_admin()

tenants = {t["tenant_id"]: t for t in load_tenants()}
if not tenants:
    st.info("Create a client first under **Admin > Clients**.")
    st.stop()
types = load_source_types()

default_tenant = st.session_state.get("tenant_id")
tenant_id = st.selectbox("Client", list(tenants), format_func=lambda t: tenants[t]["name"],
                         index=list(tenants).index(default_tenant) if default_tenant in tenants else 0)
tenant = tenants[tenant_id]

try:
    status = query("""SELECT source_name, status, rows_loaded, loaded_at, error FROM meta.source_runs WHERE tenant_id = ?
                      QUALIFY row_number() OVER (PARTITION BY source_name ORDER BY loaded_at DESC) = 1""", (tenant_id,))
except Exception:  # warehouse not built yet
    status = None
rows = []
for s in tenant["sources"]:
    last = status[status["source_name"] == s["name"]].iloc[0] if status is not None and \
        (status["source_name"] == s["name"]).any() else None
    rows.append({"source": s["name"], "system": types.get(s["type"], {}).get("system", "?"), "mapping": s["type"],
                 "connector": s["connector"]["kind"], "last status": None if last is None else last["status"],
                 "rows": None if last is None else last["rows_loaded"],
                 "last error": None if last is None else last["error"]})
st.dataframe(rows, hide_index=True, width="stretch")

NEW = "__new__"
names = [s["name"] for s in tenant["sources"]]
picked = st.selectbox("Add or edit a source", [NEW, *names],
                      format_func=lambda n: "➕ New source" if n == NEW else n, key=f"src_pick_{tenant_id}")
existing = next((s for s in tenant["sources"] if s["name"] == picked), None)
conn0 = (existing or {}).get("connector", {"kind": "file"})
k = f"{tenant_id}_{picked}"  # widget key prefix: fresh widgets per selection

with st.container(border=True):
    a, b = st.columns([1, 2])
    name = a.text_input("Source name", value=(existing or {}).get("name", ""), key=f"{k}_name",
                        help="Unique within the client; letters, digits, underscores. e.g. erp_orders")
    type_names = sorted(types)
    type_name = b.selectbox(
        "What kind of data is it? (mapping)", type_names,
        index=type_names.index(existing["type"]) if existing else 0, key=f"{k}_type",
        format_func=lambda t: f"{types[t]['system']} — {t}  ·  "
                              f"{', '.join(o['entity'] for o in types[t]['outputs'])}",
        help="How this system's columns map to the standard model. Missing? Create one under Admin > Mappings.")
    st.caption(types[type_name].get("description", ""))
    is_customer_source = any(o["entity"] == "customers" for o in types[type_name]["outputs"])
    priority = None
    if is_customer_source:
        priority = st.number_input("Golden-record priority (1 = most trusted)", 1, 100,
                                   int((existing or {}).get("priority", 10)), key=f"{k}_prio",
                                   help="When sources disagree on a customer's name, email or phone, the lower number wins.")

    kinds = {"file": "File / cloud storage", "database": "Database", "rest": "REST API"}
    kind = st.radio("Connection", list(kinds), format_func=kinds.get, horizontal=True,
                    index=list(kinds).index(conn0["kind"]), key=f"{k}_kind")
    secrets = list_names()
    OTHER = "Other (environment variable)…"

    def credential_picker(label: str, current: str | None, key: str) -> str:
        options = secrets + [OTHER]
        if current in secrets:
            idx = options.index(current)
        else:  # a name only set as an environment variable, or nothing chosen yet
            idx = len(options) - 1 if current or not secrets else 0
        choice = st.selectbox(label, options, index=idx, key=key,
                              help="Stored under Admin > Credentials (encrypted). The value is never shown here.")
        if choice == OTHER:
            return st.text_input("Credential / environment variable name", value=current or "", key=key + "_other").strip()
        return choice

    connector: dict = {"kind": kind}
    if kind == "file":
        fmt_default = conn0.get("format") or types[type_name].get("read", {}).get("format", "csv")
        formats = ["csv", "jsonl", "json", "parquet", "excel"]
        mode = st.radio("Where is the file?", ["Upload", "Path / folder / cloud URL"], horizontal=True,
                        index=0 if not conn0.get("path") or str(conn0.get("path", "")).startswith("uploads/") else 1,
                        key=f"{k}_fmode")
        fmt = st.selectbox("Format", formats, index=formats.index(fmt_default) if fmt_default in formats else 0,
                           key=f"{k}_fmt")
        if mode == "Upload":
            folder = f"uploads/{name or 'new_source'}"
            extension = "xlsx" if fmt == "excel" else fmt
            connector["path"] = f"{folder}/*.{extension}"
            files = st.file_uploader("Upload one or more files (each upload is added; re-uploading a file name "
                                     "replaces it)", accept_multiple_files=True, key=f"{k}_upload",
                                     type=["xlsx", "xls"] if fmt == "excel" else [fmt])
            if files:
                if not re.match(r"^[A-Za-z0-9_]+$", name or ""):
                    st.error("Enter a valid source name before uploading.")
                else:
                    target = SOURCES_DIR / tenant_id / folder
                    target.mkdir(parents=True, exist_ok=True)
                    for f in files:
                        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", f.name)
                        (target / safe).write_bytes(f.getbuffer())
                    st.success(f"Saved {len(files)} file(s) to {folder}/")
            existing_files = sorted(p.name for p in (SOURCES_DIR / tenant_id / folder).glob("*")) \
                if (SOURCES_DIR / tenant_id / folder).exists() else []
            if existing_files:
                st.caption("Files in this source: " + ", ".join(existing_files))
        else:
            connector["path"] = st.text_input(
                "Path, folder pattern or cloud URL", value=conn0.get("path", ""), key=f"{k}_path",
                help="Relative paths are under the client's source folder. Examples: crm/contacts.csv, "
                     "orders/*.csv, s3://bucket/sales/*.parquet, az://container/exports/*.csv")
        if fmt != types[type_name].get("read", {}).get("format", "csv"):
            connector["format"] = fmt
        if fmt == "csv":
            delim = st.text_input("Delimiter (blank = auto-detect)", value=conn0.get("read", {}).get("delimiter", ""),
                                  max_chars=1, key=f"{k}_delim")
            if delim:
                connector["read"] = {"delimiter": delim}
        if fmt == "excel":
            sheet = st.text_input("Sheet name (blank = first sheet)", value=str(conn0.get("read", {}).get("sheet", "")),
                                  key=f"{k}_sheet")
            if sheet:
                connector["read"] = {"sheet": sheet}

    elif kind == "database":
        engines = {"sqlserver": "SQL Server", "oracle": "Oracle", "postgres": "PostgreSQL", "mysql": "MySQL",
                   "odbc": "Other (ODBC)", "sqlite": "SQLite file"}
        a, b = st.columns(2)
        engine = a.selectbox("Database", list(engines), format_func=engines.get,
                             index=list(engines).index(conn0.get("engine", "sqlserver")) if conn0.get("engine") in engines else 0,
                             key=f"{k}_engine")
        with b:
            connector["connection_env"] = credential_picker("Connection string credential", conn0.get("connection_env"),
                                                            f"{k}_cred")
        hint = {"sqlserver": "Driver={ODBC Driver 18 for SQL Server};Server=tcp:host,1433;Database=db;Uid=user;Pwd=…;Encrypt=yes",
                "oracle": "Driver={Oracle ODBC Driver};Dbq=host:1521/service;Uid=user;Pwd=…",
                "postgres": "host=host port=5432 dbname=db user=user password=…",
                "mysql": "host=host port=3306 database=db user=user password=…",
                "odbc": "DSN=…;Uid=…;Pwd=…", "sqlite": "C:/path/to/file.db"}[engine]
        st.caption(f"Connection string format: `{hint}`")
        connector["engine"] = engine
        how = st.radio("Read", ["Table", "SQL query"], horizontal=True, index=1 if conn0.get("query") else 0,
                       key=f"{k}_how")
        if how == "Table":
            connector["table"] = st.text_input("Table (schema.table)", value=conn0.get("table", ""), key=f"{k}_table")
        else:
            connector["query"] = st.text_area("SQL query (read-only)", value=conn0.get("query", ""), key=f"{k}_query")
        inc = st.text_input("Incremental column (optional)", value=conn0.get("incremental", {}).get("column", ""),
                            key=f"{k}_inc", help="e.g. modified_at. Each run then fetches only rows newer than the last run.")
        if inc.strip():
            connector["incremental"] = {"column": inc.strip()}

    else:
        connector["url"] = st.text_input("Endpoint URL", value=conn0.get("url", ""), key=f"{k}_url",
                                         placeholder="https://api.example.com/v2/customers")
        a, b, c = st.columns(3)
        auth_kinds = ["None", "Bearer token", "API key header"]
        headers0 = conn0.get("headers", {})
        auth0 = "Bearer token" if headers0.get("Authorization", "").startswith("Bearer") else \
            ("API key header" if headers0 else "None")
        auth = a.selectbox("Authentication", auth_kinds, index=auth_kinds.index(auth0), key=f"{k}_auth")
        cred0 = re.search(r"\$\{(\w+)\}", " ".join(headers0.values()) or "")
        if auth != "None":
            with b:
                cred = credential_picker("Token credential", cred0.group(1) if cred0 else None, f"{k}_rcred")
            if auth == "Bearer token":
                connector["headers"] = {"Authorization": f"Bearer ${{{cred}}}"}
            else:
                header_name = c.text_input("Header name", value=next(iter(headers0), "X-API-Key"), key=f"{k}_hname")
                connector["headers"] = {header_name: f"${{{cred}}}"}
        a, b = st.columns(2)
        records_path = a.text_input("Records path in the response (blank = whole response is a list)",
                                    value=conn0.get("records_path", ""), key=f"{k}_rpath", placeholder="data.items")
        if records_path.strip():
            connector["records_path"] = records_path.strip()
        pag0 = conn0.get("pagination", {})
        pag_types = {"none": "No paging", "page": "Page number", "next_url": "Next-page link"}
        pag = b.selectbox("Paging", list(pag_types), format_func=pag_types.get,
                          index=list(pag_types).index(pag0.get("type", "none")), key=f"{k}_pag")
        if pag == "page":
            a, b, c = st.columns(3)
            connector["pagination"] = {
                "type": "page", "param": a.text_input("Page parameter", pag0.get("param", "page"), key=f"{k}_pp"),
                "size_param": b.text_input("Page-size parameter", pag0.get("size_param", "per_page"), key=f"{k}_ps"),
                "size": int(c.number_input("Page size", 1, 10000, int(pag0.get("size", 100)), key=f"{k}_pz"))}
        elif pag == "next_url":
            connector["pagination"] = {"type": "next_url", "path": st.text_input(
                "Path to the next-page URL in the response", pag0.get("path", "links.next"), key=f"{k}_np")}
        inc = st.text_input("Incremental query parameter (optional)", conn0.get("incremental", {}).get("param", ""),
                            key=f"{k}_rinc", help="e.g. updated_since: the last run's value is sent on each refresh.")
        if inc.strip():
            connector["incremental"] = {"param": inc.strip()}

source = {"name": name.strip(), "type": type_name, "connector": connector}
if priority is not None:
    source["priority"] = int(priority)

test_col, save_col, del_col = st.columns([1, 1, 1])
if test_col.button("Test connection", icon=":material/network_check:", disabled=not name.strip()):
    with st.spinner("Connecting and reading a sample…"):
        try:
            sample = preview(tenant_id, source, types[type_name], limit=50)
            st.session_state[f"{k}_sample"] = sample
            st.success(f"Connected. Read {len(sample)} sample row(s) with {len(sample.columns)} column(s).")
        except (SourceError, Exception) as exc:  # show any connector problem to the admin
            st.session_state.pop(f"{k}_sample", None)
            st.error(f"Connection failed: {exc}")

sample = st.session_state.get(f"{k}_sample")
if sample is not None:
    tab_raw, tab_mapped = st.tabs([":material/table: Raw sample", ":material/transform: After mapping"])
    with tab_raw:
        st.dataframe(sample, hide_index=True, width="stretch", height=260)
    with tab_mapped:
        try:
            for entity, df in config_store.mapping_preview(tenant, source, types[type_name], sample).items():
                filled = {c: int(df[c].notna().sum()) for c in df.columns if c not in ("source_system",)}
                empty = [c for c, n in filled.items() if n == 0]
                st.markdown(f"**{entity}**: {len(df)} row(s)" + (f" · empty columns: {', '.join(empty)}" if empty else ""))
                st.dataframe(df, hide_index=True, width="stretch", height=220)
        except Exception as exc:  # mapping expression errors come back from DuckDB
            st.error(f"The mapping '{type_name}' does not fit this data: {str(exc).splitlines()[0]}. "
                     "Pick another mapping, or adjust it under Admin > Mappings.")

if save_col.button("Save source", type="primary", icon=":material/save:", disabled=not name.strip()):
    if picked == NEW and name.strip() in names:
        st.error(f"A source named '{name.strip()}' already exists for this client.")
    else:
        try:
            config_store.save_source(tenant_id, source, None if picked == NEW else picked, admin_user())
            st.success(f"Saved. It will be loaded on the next pipeline run (Data pipeline > Run now).")
        except ConfigError as exc:
            st.error("Not saved:\n\n- " + "\n- ".join(exc.problems))

if existing:
    with del_col.popover("Remove source", icon=":material/delete:"):
        st.write(f"Remove **{picked}** from {tenant['name']}? Its data leaves the dashboards after the next run. "
                 "Uploaded files are kept.")
        if st.button("Remove", type="primary", key=f"{k}_del"):
            config_store.delete_source(tenant_id, picked, admin_user())
            st.rerun()
