import io
import re

import pandas as pd
import streamlit as st

from auth import admin_user, require_admin
from cdp import config_store
from cdp.config import ConfigError, load_source_types, load_tenants, validate
from cdp.connectors import SourceError, preview
from cdp.model import ENTITIES

st.markdown("## Mappings")
st.caption("A mapping says how one kind of system's columns become the standard customer data model. Build it "
           "once per system (e.g. 'Acme ERP orders') and reuse it for every client that has that system.")
require_admin()

types = load_source_types()
tenants = {t["tenant_id"]: t for t in load_tenants()}

SYNONYMS = {
    "source_key": ["id", "customerid", "custid", "customerno", "contactid", "clientid", "accountid", "userid"],
    "email": ["email", "emailaddress", "mail", "emailid", "useremail", "requesteremail"],
    "phone": ["phone", "mobile", "mobileno", "phonenumber", "contactno", "telephone", "customerphone", "cell"],
    "first_name": ["firstname", "fname", "givenname"], "last_name": ["lastname", "lname", "surname", "familyname"],
    "full_name": ["fullname", "name", "customername", "contactname"], "city": ["city", "town", "mailingcity"],
    "region": ["region", "state", "territory", "zone"], "created_at": ["createdat", "createddate", "created", "signupdate"],
    "order_id": ["orderid", "orderno", "ordernumber", "invoiceno", "receiptno", "billno"],
    "customer_source_key": ["customerid", "custid", "customerno", "clientid"],
    "order_ts": ["orderdate", "orderts", "createdat", "txndatetime", "invoicedate", "date"],
    "status": ["status", "orderstatus", "state", "financialstatus"], "channel": ["channel", "saleschannel", "sourcename"],
    "store_code": ["storecode", "store", "storeid", "branch"], "order_value": ["total", "ordertotal", "amount", "netamount"],
    "line_id": ["lineid", "orderlineid", "receiptline", "itemid"], "sku": ["sku", "productcode", "itemcode", "productid"],
    "quantity": ["quantity", "qty", "units"], "line_amount": ["lineamount", "amount", "linetotal", "price"],
    "product_name": ["productname", "title", "name", "itemname"], "category": ["category", "productcategory"],
    "list_price": ["listprice", "price", "mrp"], "unit_cost": ["unitcost", "cost"], "event_id": ["eventid", "id"],
    "event_type": ["eventtype", "event", "action"], "event_ts": ["timestamp", "eventtime", "eventts", "time"],
    "session_id": ["sessionid", "anonymousid", "visitid"], "device": ["device", "devicetype", "platform"],
    "ticket_id": ["ticketid", "id", "caseid"], "issue_type": ["subject", "issuetype", "category", "topic"],
    "priority": ["priority"], "csat": ["csat", "satisfactionscore", "rating"],
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def suggest(column: str, raw_columns: list[str]) -> str:
    by_norm = {_norm(c): c for c in raw_columns}
    for candidate in [column, *SYNONYMS.get(column, [])]:
        if _norm(candidate) in by_norm:
            return by_norm[_norm(candidate)]
    return ""


def quote_ident(column: str) -> str:
    return '"' + column.replace('"', '""') + '"'


def split_expression(expr: str | None, raw_columns: list[str]) -> tuple[str, str]:
    """Existing expression -> (picked raw column, custom expression)."""
    if not expr:
        return "", ""
    bare = expr[1:-1].replace('""', '"') if expr.startswith('"') and expr.endswith('"') else expr
    return (bare, "") if bare in raw_columns else ("", expr)


def read_upload(upload, fmt: str) -> pd.DataFrame:
    data = io.BytesIO(upload.getbuffer())
    if fmt == "csv":
        return pd.read_csv(data, dtype=str, nrows=200, sep=None, engine="python")
    if fmt == "jsonl":
        return pd.read_json(data, lines=True, dtype=False, nrows=200).astype("string")
    if fmt == "json":
        return pd.json_normalize(pd.read_json(data).to_dict("records"), sep="_").head(200).astype("string")
    if fmt == "parquet":
        return pd.read_parquet(data).head(200).astype("string")
    return pd.read_excel(data, dtype=str, nrows=200)


# --------------------------------------------------------------------------- pick a mapping
NEW = "__new__"
picked = st.selectbox("Mapping", [NEW, *sorted(types)],
                      format_func=lambda t: "➕ New mapping" if t == NEW else f"{types[t]['system']} — {t}")
current = types.get(picked, {"system": "", "description": "", "read": {"format": "csv"}, "outputs": []})
k = f"map_{picked}"

with st.container(border=True):
    a, b = st.columns(2)
    map_name = a.text_input("Mapping id", value="" if picked == NEW else picked, disabled=picked != NEW,
                            key=f"{k}_id", help="lowercase, e.g. acme_erp_orders")
    system = b.text_input("System name (shown to users)", value=current.get("system", ""), key=f"{k}_sys",
                          placeholder="e.g. Acme ERP")
    description = st.text_input("Description", value=current.get("description", ""), key=f"{k}_desc",
                                placeholder="What export / table this mapping is for")

# --------------------------------------------------------------------------- sample data
st.markdown("#### 1. Load sample data")
users = [(t, s) for t in tenants.values() for s in t["sources"] if s["type"] == picked]
mode = st.radio("Sample from", ["A configured source", "Upload a sample file"], horizontal=True,
                index=0 if users else 1, key=f"{k}_smode")
if mode == "Upload a sample file":
    a, b = st.columns([1, 3])
    up_fmt = a.selectbox("Format", ["csv", "jsonl", "json", "parquet", "excel"],
                         index=["csv", "jsonl", "json", "parquet", "excel"].index(current.get("read", {}).get("format", "csv")),
                         key=f"{k}_upfmt")
    upload = b.file_uploader("Sample file", key=f"{k}_up")
    if upload is not None:
        try:
            st.session_state[f"{k}_sample"] = read_upload(upload, up_fmt)
            st.session_state[f"{k}_fmt"] = up_fmt
        except Exception as exc:  # pandas parsing errors vary by format
            st.error(f"Could not read the file as {up_fmt}: {exc}")
else:
    all_sources = [(t, s) for t in tenants.values() for s in t["sources"]]
    if not all_sources:
        st.info("No sources configured yet; upload a sample file instead.")
    else:
        default = all_sources.index(users[0]) if users else 0
        idx = st.selectbox("Source", range(len(all_sources)), index=default, key=f"{k}_ssrc",
                           format_func=lambda i: f"{all_sources[i][0]['name']} / {all_sources[i][1]['name']}")
        if st.button("Read sample", icon=":material/download:", key=f"{k}_read"):
            t, s = all_sources[idx]
            try:
                st.session_state[f"{k}_sample"] = preview(t["tenant_id"], s, types.get(s["type"]), limit=200)
            except SourceError as exc:
                st.error(f"Could not read the source: {exc}")

sample: pd.DataFrame | None = st.session_state.get(f"{k}_sample")
raw_columns = list(sample.columns) if sample is not None else []
if sample is not None:
    with st.expander(f"Sample: {len(sample)} rows · {len(raw_columns)} columns", expanded=False):
        st.dataframe(sample.head(50), hide_index=True, width="stretch")
elif not current["outputs"]:
    st.info("Load a sample to see the source columns.")

# --------------------------------------------------------------------------- outputs
st.markdown("#### 2. Map columns to the standard model")
outputs_key = f"{k}_outputs"
if outputs_key not in st.session_state:
    st.session_state[outputs_key] = [dict(o) for o in current["outputs"]]
outputs: list[dict] = st.session_state[outputs_key]

a, b = st.columns([2, 1])
new_entity = a.selectbox("Add what this data contains", list(ENTITIES), key=f"{k}_newent",
                         help="One source can feed several entities, e.g. a POS export -> orders + order lines.")
if b.button("Add", icon=":material/add:", key=f"{k}_add"):
    outputs.append({"entity": new_entity, "columns": {}, "_auto": True})
    st.rerun()

built_outputs = []
if outputs:
    tabs = st.tabs([f"{i + 1}. {o['entity']}" for i, o in enumerate(outputs)])
for i, (tab, out) in enumerate(zip(tabs, outputs) if outputs else []):
    with tab:
        entity = out["entity"]
        spec = ENTITIES[entity]
        rows = []
        for col, (dtype, required, desc) in spec.items():
            picked_col, custom = split_expression(out["columns"].get(col), raw_columns)
            if out.get("_auto") and not out["columns"].get(col):
                picked_col = suggest(col, raw_columns)
            rows.append({"standard column": col, "required": required, "type": dtype, "meaning": desc,
                         "from source column": picked_col or None, "or SQL expression": custom or None})
        edited = st.data_editor(
            pd.DataFrame(rows), hide_index=True, width="stretch", key=f"{k}_ed_{i}_{len(raw_columns)}",
            disabled=["standard column", "required", "type", "meaning"],
            column_config={
                "required": st.column_config.CheckboxColumn("req.", width="small"),
                "from source column": st.column_config.SelectboxColumn(options=raw_columns, width="medium"),
                "or SQL expression": st.column_config.TextColumn(
                    help="DuckDB SQL over the source columns, e.g. strptime(OrderDate, '%d/%m/%Y') or "
                         "CASE WHEN Cancelled = '1' THEN 'cancelled' ELSE 'completed' END. Wins over the dropdown."),
            })
        columns = {}
        for r in edited.itertuples(index=False):  # (standard column, required, type, meaning, column, expression)
            col, expr = r[4], r[5]
            if isinstance(expr, str) and expr.strip():
                columns[r[0]] = expr.strip()
            elif isinstance(col, str) and col:
                columns[r[0]] = quote_ident(col)
        built = {"entity": entity, "columns": columns}
        c1, c2, c3 = st.columns(3)
        if entity == "customers":
            built["namespace"] = c1.text_input(
                "Id namespace", value=out.get("namespace", _norm(system) or "source"), key=f"{k}_ns_{i}",
                help="Name for this system's customer ids. Other sources that reference these ids use the same name.")
        if "customer_source_key" in columns:
            built["customer_namespace"] = c2.text_input(
                "Customer ids belong to namespace", value=out.get("customer_namespace", _norm(system)),
                key=f"{k}_cns_{i}", help="The 'Id namespace' of the customer source these ids point to.")
        saved_group_by = [g.strip('"') for g in out.get("group_by", [])]
        group_help = ("Only for line-level data that should become one row per order, e.g. receipt_no. "
                      "Expressions must then be aggregates (min, sum, any_value...).")
        if raw_columns:
            # Key includes the sample's columns so the widget re-initialises when a sample is loaded.
            group_by = c3.multiselect("One row per (group by)", raw_columns,
                                      default=[g for g in saved_group_by if g in raw_columns],
                                      key=f"{k}_gb_{i}_{len(raw_columns)}", help=group_help)
        else:
            group_by = saved_group_by  # keep the saved setting until a sample shows the columns
            if group_by:
                c3.caption(f"One row per: {', '.join(group_by)}")
        if group_by:
            built["group_by"] = [quote_ident(g) for g in group_by]
        if out.get("where"):
            built["where"] = out["where"]
        if st.button("Remove this output", key=f"{k}_rm_{i}", icon=":material/close:"):
            outputs.pop(i)
            st.rerun()
        built_outputs.append(built)

source_type = {"description": description.strip(), "system": system.strip(),
               "read": {**current.get("read", {}), "format": st.session_state.get(f"{k}_fmt",
                                                                                   current.get("read", {}).get("format", "csv"))},
               "outputs": built_outputs}

# --------------------------------------------------------------------------- check + preview
st.markdown("#### 3. Check and save")
problems = validate([], {map_name or "new": source_type}) if built_outputs else ["Add at least one output."]
if not system.strip():
    problems.append("Enter the system name.")
for p in problems:
    st.warning(p.split(": ", 1)[-1])

if sample is not None and built_outputs:
    try:
        results = config_store.mapping_preview({"tenant_id": "preview"}, {"name": "sample"}, source_type, sample)
        for entity, df in results.items():
            st.markdown(f"**Result: {entity}** ({len(df)} rows from the sample)")
            st.dataframe(df.head(20), hide_index=True, width="stretch")
    except Exception as exc:  # DuckDB reports the failing expression
        st.error(f"Mapping error: {str(exc).splitlines()[0]}")

a, b = st.columns(2)
if a.button("Save mapping", type="primary", icon=":material/save:", disabled=bool(problems) or not map_name.strip()):
    try:
        config_store.save_source_type(map_name.strip(), source_type, admin_user())
        st.session_state.pop(outputs_key, None)
        st.success(f"Saved mapping '{map_name.strip()}'. Use it when adding a source under Admin > Sources.")
    except ConfigError as exc:
        st.error("Not saved:\n\n- " + "\n- ".join(exc.problems))
if picked != NEW:
    with b.popover("Delete mapping", icon=":material/delete:"):
        if users:
            st.write("In use by: " + ", ".join(f"{t['name']}/{s['name']}" for t, s in users) +
                     ". Remove those sources first.")
        elif st.button("Delete", type="primary", key=f"{k}_delete"):
            config_store.delete_source_type(picked, admin_user())
            st.session_state.pop(outputs_key, None)
            st.rerun()
