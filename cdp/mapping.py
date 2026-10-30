"""Mapping engine: generates the standardized silver layer from source-type mapping files.

For every tenant source and every output in its source type, a SQL SELECT is generated that maps the
raw bronze columns to one standard entity (customers, orders, order_lines, products, events, tickets).
All outputs of the same entity are then unioned into silver.std_<entity>, with normalized
email/phone and an `identity_key` that identity resolution links into customers.

A mapping error (e.g. a renamed column in the source) fails only that source, with a clear message.
"""
from __future__ import annotations

import logging

import duckdb

from cdp.model import ENTITIES, IDENTITY_ENTITIES, PRIMARY_KEYS

log = logging.getLogger("cdp.mapping")


def bronze_table(tenant_id: str, source_name: str) -> str:
    return f'bronze."{tenant_id}__{source_name}"'


def _lit(value) -> str:
    return "NULL" if value is None else "'" + str(value).replace("'", "''") + "'"


def output_sql(tenant: dict, source: dict, source_type: dict, output: dict) -> str:
    entity = output["entity"]
    mapped = output["columns"]
    grouped = bool(output.get("group_by"))
    select = [
        f"{_lit(tenant['tenant_id'])} AS tenant_id",
        f"{_lit(source['name'])} AS source_name",
        f"{_lit(source_type['system'])} AS source_system",
        f"{_lit(output.get('namespace') or source_type['system'].lower())} AS namespace",
        f"{_lit(output.get('customer_namespace'))} AS customer_namespace",
    ]
    for column, (dtype, _, _) in ENTITIES[entity].items():
        expr = mapped.get(column)
        select.append(f"CAST(({expr}) AS {dtype}) AS {column}" if expr else f"CAST(NULL AS {dtype}) AS {column}")
    select.append("max(_loaded_at) AS _loaded_at" if grouped else "_loaded_at")
    sql = f"SELECT {', '.join(select)} FROM {bronze_table(tenant['tenant_id'], source['name'])}"
    if output.get("where"):
        sql += f" WHERE {output['where']}"
    if grouped:
        sql += " GROUP BY " + ", ".join(output["group_by"])
    pk = PRIMARY_KEYS[entity]
    # Keep the latest version of each record (incremental loads append new versions).
    return (f"SELECT * FROM ({sql}) WHERE {pk} IS NOT NULL "
            f"QUALIFY row_number() OVER (PARTITION BY {pk} ORDER BY _loaded_at DESC) = 1")


def _empty_entity_sql(entity: str) -> str:
    cols = ["CAST(NULL AS VARCHAR) AS tenant_id", "CAST(NULL AS VARCHAR) AS source_name",
            "CAST(NULL AS VARCHAR) AS source_system", "CAST(NULL AS VARCHAR) AS namespace",
            "CAST(NULL AS VARCHAR) AS customer_namespace"]
    cols += [f"CAST(NULL AS {dtype}) AS {c}" for c, (dtype, _, _) in ENTITIES[entity].items()]
    cols.append("CAST(NULL AS TIMESTAMP) AS _loaded_at")
    return f"SELECT {', '.join(cols)} WHERE false"


def _normalize_sql(entity: str, union_sql: str) -> str:
    extra = []
    if "email" in ENTITIES[entity]:
        extra.append("nullif(lower(trim(e.email)), '') AS email_norm")
    if "phone" in ENTITIES[entity]:
        digits = "regexp_replace(coalesce(e.phone, ''), '[^0-9]', '', 'g')"
        extra.append(f"CASE WHEN length({digits}) >= t.phone_digits THEN right({digits}, t.phone_digits) END AS phone_norm")
    if entity == "customers":
        extra.append("e.namespace || ':' || e.source_key AS identity_key")
    elif entity in IDENTITY_ENTITIES:
        extra.append(
            "CASE WHEN e.customer_source_key IS NOT NULL THEN e.customer_namespace || ':' || e.customer_source_key "
            "WHEN nullif(lower(trim(e.email)), '') IS NOT NULL THEN 'email:' || lower(trim(e.email)) "
            f"WHEN length({digits}) >= t.phone_digits THEN 'phone:' || right({digits}, t.phone_digits) "
            "END AS identity_key")
    extra_sql = (", " + ", ".join(extra)) if extra else ""
    return (f"SELECT e.*{extra_sql} FROM ({union_sql}) e "
            "LEFT JOIN meta.tenants t ON t.tenant_id = e.tenant_id")


def build_standard_layer(con: duckdb.DuckDBPyConnection, tenants: list[dict], source_types: dict[str, dict],
                         available: set[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """Build silver.std_<entity> tables. Returns {(tenant, source): error} for sources whose mapping failed.

    `available` holds the (tenant, source) pairs that have a bronze table.
    """
    errors: dict[tuple[str, str], str] = {}
    parts: dict[str, list[str]] = {entity: [] for entity in ENTITIES}
    for tenant in tenants:
        for source in tenant["sources"]:
            key = (tenant["tenant_id"], source["name"])
            if key not in available:
                continue
            st = source_types[source["type"]]
            ok: list[tuple[str, str]] = []
            for output in st["outputs"]:
                sql = output_sql(tenant, source, st, output)
                try:
                    con.execute(f"SELECT * FROM ({sql}) LIMIT 0")  # validate against the real bronze columns
                    ok.append((output["entity"], sql))
                except duckdb.Error as exc:
                    msg = str(exc).split("\n")[0]
                    errors[key] = f"mapping to {output['entity']} failed: {msg}"
                    log.error("Mapping failed for %s/%s -> %s: %s", *key, output["entity"], msg)
            if key not in errors:  # a source is used only when all of its outputs map cleanly
                for entity, sql in ok:
                    parts[entity].append(sql)
    for entity, selects in parts.items():
        union = " UNION ALL BY NAME ".join(f"({s})" for s in selects) if selects else _empty_entity_sql(entity)
        con.execute(f"CREATE OR REPLACE TABLE silver.std_{entity} AS {_normalize_sql(entity, union)}")
    return errors
