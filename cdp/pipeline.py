"""Pipeline: connect -> map to the standard model -> resolve identities -> Customer 360 + marts.

Layers (one DuckDB warehouse):
  bronze."<tenant>__<source>"  raw copy of each source exactly as extracted, plus load metadata
  silver.std_<entity>          every source mapped to the standard model (generated from config)
  silver.*                     identity map, golden customer record, transactions, tickets, sessions
  gold.*                       Customer 360, RFM/segments, retention, funnel, products, revenue
  meta.*                       config snapshot, run + per-source history, watermarks, model catalog, DQ

Production behaviour
  * One failing source never stops the others: it is logged, recorded in meta.source_runs, and the
    previous bronze data for that source keeps being used. The run ends as "partial".
  * A full-refresh extract that returns 0 rows does not wipe the existing data (status "warning").
  * Incremental sources append only new rows (watermark per source) and are de-duplicated by key.
  * Zero-downtime refresh: each run works on a copy of the warehouse and swaps it in only when it
    finishes, so the app keeps serving the previous data and a failed run never touches live data.

CLI:  python -m cdp run [--tenant ID ...] [--full-refresh]
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

from cdp.config import (AS_OF_DATE, SQL_DIR, WAREHOUSE_PATH, business_rules, load_source_types, load_tenants)
from cdp.connectors import ExtractContext, SourceError, extract
from cdp.identity import resolve_identities
from cdp.mapping import bronze_table, build_standard_layer

log = logging.getLogger("cdp.pipeline")
BUILD_PATH = WAREHOUSE_PATH.with_name(WAREHOUSE_PATH.stem + ".building" + WAREHOUSE_PATH.suffix)


def connect(read_only: bool = False, path: Path = WAREHOUSE_PATH) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return duckdb.connect(str(path), read_only=read_only)
    except duckdb.IOException as exc:
        raise RuntimeError(f"Cannot open the warehouse {path} - is another pipeline run using it?") from exc


def _start_build() -> None:
    if BUILD_PATH.exists():
        raise RuntimeError(f"{BUILD_PATH.name} exists: another run is in progress, or a previous run crashed "
                           "(delete the file if no run is active).")
    if WAREHOUSE_PATH.exists():
        shutil.copy2(WAREHOUSE_PATH, BUILD_PATH)  # keeps bronze history, watermarks and run history


def _publish_build(attempts: int = 30) -> None:
    """Atomically replace the live warehouse with the finished build (retries while readers hold it on Windows)."""
    for attempt in range(attempts):
        try:
            os.replace(BUILD_PATH, WAREHOUSE_PATH)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(1)


# --------------------------------------------------------------------------- metadata
def init_schemas(con: duckdb.DuckDBPyConnection) -> None:
    for schema in ("bronze", "silver", "gold", "meta"):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    con.execute("""
        CREATE TABLE IF NOT EXISTS meta.runs (
            run_id VARCHAR, started_at TIMESTAMP, finished_at TIMESTAMP, status VARCHAR, as_of_date DATE,
            tenants VARCHAR, message VARCHAR);
        CREATE TABLE IF NOT EXISTS meta.source_runs (
            run_id VARCHAR, tenant_id VARCHAR, source_name VARCHAR, source_type VARCHAR, source_system VARCHAR,
            connector VARCHAR, load_mode VARCHAR, status VARCHAR, rows_loaded BIGINT, seconds DOUBLE,
            error VARCHAR, loaded_at TIMESTAMP);
        CREATE TABLE IF NOT EXISTS meta.watermarks (
            tenant_id VARCHAR, source_name VARCHAR, column_name VARCHAR, value VARCHAR, updated_at TIMESTAMP,
            PRIMARY KEY (tenant_id, source_name));
        CREATE TABLE IF NOT EXISTS meta.models (
            name VARCHAR PRIMARY KEY, layer VARCHAR, description VARCHAR, depends_on VARCHAR, row_count BIGINT,
            seconds DOUBLE, built_at TIMESTAMP);
    """)


def snapshot_config(con: duckdb.DuckDBPyConnection, tenants: list[dict]) -> None:
    """Store the active configuration so SQL models and the app can use it."""
    source_types = load_source_types()
    tenant_rows, source_rows, rule_rows = [], [], []
    for t in tenants:
        tenant_rows.append({"tenant_id": t["tenant_id"], "tenant_name": t["name"], "currency": t["currency"],
                            "industry": t.get("industry"), "phone_digits": int(t.get("phone_digits", 10))})
        for s in t["sources"]:
            source_rows.append({
                "tenant_id": t["tenant_id"], "source_name": s["name"], "source_type": s["type"],
                "source_system": source_types[s["type"]]["system"], "connector": s["connector"]["kind"],
                "load_mode": "incremental" if s["connector"].get("incremental") else "full",
                "priority": int(s.get("priority", 100)),
                "entities": ", ".join(o["entity"] for o in source_types[s["type"]]["outputs"]),
            })
        r = business_rules(t["tenant_id"])
        rule_rows.append({
            "tenant_id": t["tenant_id"], "tenant_name": t["name"], "currency": t["currency"],
            "as_of_date": AS_OF_DATE, "active_window_days": int(r["active_window_days"]),
            "churn_risk_high_days": int(r["churn_risk_high_days"]),
            "churn_risk_medium_days": int(r["churn_risk_medium_days"]),
            "recent_activity_days": int(r["recent_activity_days"]),
            "tier_platinum_min": float(r["tier_thresholds"]["Platinum"]),
            "tier_gold_min": float(r["tier_thresholds"]["Gold"]),
            "tier_silver_min": float(r["tier_thresholds"]["Silver"]),
        })
    for name, rows in (("tenants", tenant_rows), ("sources", source_rows), ("business_rules", rule_rows)):
        frame = pd.DataFrame(rows)  # noqa: F841 - read by DuckDB below
        con.execute(f"CREATE OR REPLACE TABLE meta.{name} AS SELECT * FROM frame")
    con.execute("ALTER TABLE meta.business_rules ALTER as_of_date TYPE DATE")
    con.execute("ALTER TABLE meta.business_rules ALTER active_window_days TYPE INTEGER")
    con.execute("ALTER TABLE meta.business_rules ALTER churn_risk_high_days TYPE INTEGER")
    con.execute("ALTER TABLE meta.business_rules ALTER churn_risk_medium_days TYPE INTEGER")
    con.execute("ALTER TABLE meta.business_rules ALTER recent_activity_days TYPE INTEGER")


def _bronze_exists(con: duckdb.DuckDBPyConnection, tenant_id: str, source_name: str) -> bool:
    return con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'bronze' AND table_name = ?",
        [f"{tenant_id}__{source_name}"]).fetchall()[0][0] > 0


# --------------------------------------------------------------------------- bronze
def ingest_source(con, run_id: str, tenant: dict, source: dict, full_refresh: bool) -> dict:
    source_type = load_source_types()[source["type"]]
    conn = source["connector"]
    inc_col = conn.get("incremental", {}).get("column")
    incremental = bool(inc_col) and not full_refresh
    target = bronze_table(tenant["tenant_id"], source["name"])
    watermark = None
    if incremental:
        rows = con.execute("SELECT value FROM meta.watermarks WHERE tenant_id = ? AND source_name = ?",
                           [tenant["tenant_id"], source["name"]]).fetchall()
        watermark = rows[0][0] if rows else None

    result = {"run_id": run_id, "tenant_id": tenant["tenant_id"], "source_name": source["name"],
              "source_type": source["type"], "source_system": source_type["system"], "connector": conn["kind"],
              "load_mode": "incremental" if incremental else "full", "status": "success", "rows_loaded": 0,
              "seconds": 0.0, "error": None}
    started = time.perf_counter()
    try:
        rows = extract(ExtractContext(con, tenant["tenant_id"], source, source_type, watermark))
        result["rows_loaded"] = rows
        exists = _bronze_exists(con, tenant["tenant_id"], source["name"])
        load_cols = f"*, '{run_id}' AS _run_id, now()::TIMESTAMP AS _loaded_at"
        if rows == 0 and (incremental or exists):
            if not incremental:
                result["status"] = "warning"
                result["error"] = "Source returned 0 rows; kept the previously loaded data"
        elif incremental and exists:
            con.execute(f"INSERT INTO {target} BY NAME SELECT {load_cols} FROM _extract")
        else:
            con.execute(f"CREATE OR REPLACE TABLE {target} AS SELECT {load_cols} FROM _extract")
        if inc_col and rows:
            new_mark = con.execute(f"SELECT CAST(max({inc_col}) AS VARCHAR) FROM _extract").fetchall()[0][0]
            con.execute("INSERT OR REPLACE INTO meta.watermarks VALUES (?, ?, ?, ?, now())",
                        [tenant["tenant_id"], source["name"], inc_col, new_mark])
    except (SourceError, duckdb.Error, OSError, ValueError) as exc:
        result["status"] = "failed"
        result["error"] = str(exc).split("\n")[0][:500]
        log.error("Source %s/%s failed: %s", tenant["tenant_id"], source["name"], result["error"])
    result["seconds"] = round(time.perf_counter() - started, 3)
    if result["status"] != "failed":
        log.info("bronze %-34s %9s rows  (%s, %s)", f"{tenant['tenant_id']}/{source['name']}",
                 f"{result['rows_loaded']:,}", result["load_mode"], result["status"])
    return result


# --------------------------------------------------------------------------- SQL models
_HEADER = re.compile(r"^--\s*(\w+):\s*(.*)$")


def _parse_model(path) -> dict:
    meta = {"description": "", "depends_on": ""}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _HEADER.match(line)
        if not m:
            break
        meta[m.group(1)] = m.group(2).strip()
    return {"name": f"{path.parent.name}.{path.stem.split('_', 1)[1]}", "path": path, **meta}


def run_models(con: duckdb.DuckDBPyConnection, layer: str) -> None:
    for path in sorted((SQL_DIR / layer).glob("*.sql")):
        model = _parse_model(path)
        started = time.perf_counter()
        try:
            con.execute(path.read_text(encoding="utf-8"))
        except duckdb.Error as exc:
            raise RuntimeError(f"Model {model['name']} ({path.name}) failed: {exc}") from exc
        rows = con.execute(f"SELECT count(*) FROM {model['name']}").fetchall()[0][0]
        con.execute("INSERT OR REPLACE INTO meta.models VALUES (?, ?, ?, ?, ?, ?, now())", [
            model["name"], layer, model["description"], model["depends_on"], rows,
            round(time.perf_counter() - started, 3)])
        log.info("model  %-34s %9s rows", model["name"], f"{rows:,}")


def record_standard_models(con: duckdb.DuckDBPyConnection) -> None:
    sources = con.execute("SELECT entities, tenant_id || '/' || source_name FROM meta.sources").fetchall()
    for entity in ("customers", "orders", "order_lines", "products", "events", "tickets"):
        deps = sorted({f"bronze.{name.split('/')[1]}" for ents, name in sources if entity in ents.split(", ")})
        rows = con.execute(f"SELECT count(*) FROM silver.std_{entity}").fetchall()[0][0]
        con.execute("INSERT OR REPLACE INTO meta.models VALUES (?, 'silver', ?, ?, ?, 0, now())", [
            f"silver.std_{entity}", f"All sources mapped to the standard '{entity}' entity (generated from config).",
            ", ".join(deps), rows])


# --------------------------------------------------------------------------- data quality
DQ_CHECKS = [
    ("Orders linked to a customer", "silver.transactions",
     "SELECT tenant_id, avg(CASE WHEN customer_id IS NOT NULL THEN 1 ELSE 0 END) FROM silver.transactions GROUP BY 1", 0.9),
    ("Order status values are valid", "silver.transactions",
     "SELECT tenant_id, avg(CASE WHEN status IN ('completed', 'refunded', 'cancelled') THEN 1 ELSE 0 END) "
     "FROM silver.transactions GROUP BY 1", 1.0),
    ("Orders have a value", "silver.transactions",
     "SELECT tenant_id, avg(CASE WHEN order_value > 0 OR status = 'cancelled' THEN 1 ELSE 0 END) "
     "FROM silver.transactions GROUP BY 1", 0.98),
    ("Customers with a valid email", "silver.customers",
     "SELECT tenant_id, avg(CASE WHEN email IS NOT NULL THEN 1 ELSE 0 END) FROM silver.customers GROUP BY 1", 0.8),
    ("Unique order ids", "silver.transactions",
     "SELECT tenant_id, count(DISTINCT order_id) / count(*) FROM silver.transactions GROUP BY 1", 1.0),
    ("Order lines with a known product", "silver.transaction_lines",
     "SELECT l.tenant_id, avg(CASE WHEN p.sku IS NOT NULL THEN 1 ELSE 0 END) FROM silver.transaction_lines l "
     "LEFT JOIN silver.products p USING (tenant_id, sku) GROUP BY 1", 0.99),
    ("Tickets linked to a customer", "silver.support_tickets",
     "SELECT tenant_id, avg(CASE WHEN customer_id IS NOT NULL THEN 1 ELSE 0 END) FROM silver.support_tickets GROUP BY 1",
     0.95),
    ("Order dates not in the future", "silver.transactions",
     "SELECT t.tenant_id, avg(CASE WHEN t.order_date <= r.as_of_date THEN 1 ELSE 0 END) FROM silver.transactions t "
     "JOIN meta.business_rules r USING (tenant_id) GROUP BY 1", 0.99),
]


def run_quality_checks(con: duckdb.DuckDBPyConnection, run_id: str) -> int:
    rows = []
    for name, table, sql, threshold in DQ_CHECKS:
        for tenant_id, value in con.execute(sql).fetchall():
            value = float(value or 0)
            rows.append({"run_id": run_id, "tenant_id": tenant_id, "check_name": name, "table_name": table,
                         "value": value, "threshold": threshold,
                         "status": "pass" if value >= threshold - 1e-9 else "warn"})
    dq = pd.DataFrame(rows)  # noqa: F841
    con.execute("CREATE OR REPLACE TABLE meta.dq_results AS SELECT *, now()::TIMESTAMP AS checked_at FROM dq")
    warnings = sum(1 for r in rows if r["status"] != "pass")
    if warnings:
        log.warning("%d data-quality check(s) below target - see meta.dq_results", warnings)
    return warnings


# --------------------------------------------------------------------------- orchestration
def run(tenant_ids: list[str] | None = None, full_refresh: bool = False) -> str:
    """Run the pipeline. Returns the final status: success, partial or failed."""
    tenants = load_tenants()  # validates config first; raises ConfigError with every problem
    known = {t["tenant_id"] for t in tenants}
    unknown = set(tenant_ids or []) - known
    if unknown:
        raise ValueError(f"Unknown tenant(s): {sorted(unknown)}; configured: {sorted(known)}")
    to_ingest = [t for t in tenants if not tenant_ids or t["tenant_id"] in tenant_ids]

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:4]
    started = datetime.now()
    _start_build()
    con = connect(path=BUILD_PATH)
    status, message = "failed", ""
    try:
        init_schemas(con)
        con.execute("INSERT INTO meta.runs VALUES (?, ?, NULL, 'running', ?, ?, NULL)",
                    [run_id, started, AS_OF_DATE, ", ".join(t["tenant_id"] for t in to_ingest)])
        log.info("Run %s started: ingesting %s (as-of %s)", run_id, [t["tenant_id"] for t in to_ingest], AS_OF_DATE)
        snapshot_config(con, tenants)

        results = [ingest_source(con, run_id, t, s, full_refresh) for t in to_ingest for s in t["sources"]]

        # Models are always rebuilt for all tenants from whatever bronze data exists.
        available = {(t["tenant_id"], s["name"]) for t in tenants for s in t["sources"]
                     if _bronze_exists(con, t["tenant_id"], s["name"])}
        mapping_errors = build_standard_layer(con, tenants, load_source_types(), available)
        for r in results:
            if (r["tenant_id"], r["source_name"]) in mapping_errors:
                r["status"], r["error"] = "failed", mapping_errors[(r["tenant_id"], r["source_name"])]
        source_runs = pd.DataFrame(results)  # noqa: F841
        con.execute("INSERT INTO meta.source_runs BY NAME SELECT *, now()::TIMESTAMP AS loaded_at FROM source_runs")
        record_standard_models(con)

        stats = resolve_identities(con)
        log.info("identity: %s", stats)
        run_models(con, "silver")
        run_models(con, "gold")
        dq_warnings = run_quality_checks(con, run_id)

        failed = [f"{r['tenant_id']}/{r['source_name']}" for r in results if r["status"] == "failed"]
        status = "partial" if failed else "success"
        message = (f"Failed sources: {', '.join(failed)}. " if failed else "") + \
                  (f"{dq_warnings} data-quality warning(s)." if dq_warnings else "")
    except Exception as exc:
        message = str(exc).split("\n")[0][:500]
        log.exception("Run %s failed", run_id)
        raise
    finally:
        con.execute("UPDATE meta.runs SET finished_at = now(), status = ?, message = ? WHERE run_id = ?",
                    [status, message or None, run_id])
        con.close()
        if status == "failed":
            BUILD_PATH.unlink(missing_ok=True)  # live warehouse stays exactly as it was
        else:
            _publish_build()
        log.info("Run %s finished: %s in %.1fs %s", run_id, status.upper(),
                 (datetime.now() - started).total_seconds(), message)
    return status


def schedule(every_minutes: int) -> None:
    """Run forever, every N minutes. A failed run is logged and retried at the next slot."""
    while True:
        next_at = time.time() + every_minutes * 60
        try:
            run()
        except Exception:  # already logged with traceback inside run()
            log.error("Scheduled run failed; next attempt in %d minutes", every_minutes)
        time.sleep(max(0.0, next_at - time.time()))
