"""Connectors: pull one source into a temporary DuckDB table `_extract`, exactly as the source has it.

Connector kinds (set in each tenant source's "connector" block):

  file      Local files, folders/globs (e.g. "orders/*.csv"), or cloud URIs:
            s3://..., az://... / abfss://... (Azure Blob / ADLS Gen2), gs://..., https://...
            Formats: csv, jsonl, json, parquet, excel.
  database  engine = "odbc" (SQL Server, Oracle, DB2, Snowflake, ... anything with an ODBC driver),
            "sqlserver"/"oracle" (aliases of odbc), "postgres", "mysql" (DuckDB scanners), "sqlite".
            Give "table" or "query". The connection string is read from the environment variable named
            in "connection_env" - credentials never live in config files.
  rest      JSON REST APIs with header auth and page / next-link pagination.

Large SaaS platforms (Salesforce, HubSpot, Dynamics, ...) are best landed with a dedicated ingestion
tool (Airbyte, Fivetran, dlt, Azure Data Factory) into files or a database, which the connectors
above then read. That keeps the platform small while giving access to hundreds of systems.

Incremental loads: add "incremental": {"column": "<updated_at column>"} to a database or rest
connector. Only rows newer than the last stored watermark are fetched and appended to bronze.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
import urllib.parse
import urllib.request
from dataclasses import dataclass

import duckdb
import pandas as pd

from cdp.config import SOURCES_DIR

CLOUD_SCHEMES = ("s3://", "s3a://", "gs://", "gcs://", "az://", "azure://", "abfss://", "http://", "https://")


class SourceError(RuntimeError):
    """A single source could not be extracted; other sources still run."""


@dataclass
class ExtractContext:
    con: duckdb.DuckDBPyConnection
    tenant_id: str
    source: dict          # the tenant's source entry
    source_type: dict     # the mapping template
    watermark: str | None = None
    limit: int | None = None   # preview / test-connection mode: fetch at most this many rows

    @property
    def connector(self) -> dict:
        return self.source["connector"]


def _quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _env(name: str) -> str:
    """A credential by name: the encrypted store (admin UI) first, then the environment."""
    from cdp.secrets import get_secret
    try:
        value = get_secret(name)
    except RuntimeError as exc:
        raise SourceError(str(exc)) from exc
    if not value:
        raise SourceError(f"Credential {name} is not set (add it under Admin > Credentials, or as an environment "
                          "variable)")
    return value


def _expand_env(text: str) -> str:
    """Replace ${NAME} with the credential's value (used for headers and URLs)."""
    return re.sub(r"\$\{(\w+)\}", lambda m: _env(m.group(1)), text)


def _limit_sql(ctx: ExtractContext) -> str:
    return f" LIMIT {int(ctx.limit)}" if ctx.limit else ""


def _store(ctx: ExtractContext, df: pd.DataFrame) -> None:
    if not len(df.columns):
        ctx.con.execute("CREATE OR REPLACE TEMP TABLE _extract AS SELECT NULL AS _empty WHERE false")
        return
    df = df.astype("string")  # land everything as text; the mappings cast to proper types
    ctx.con.register("_extract_df", df)
    ctx.con.execute("CREATE OR REPLACE TEMP TABLE _extract AS SELECT * FROM _extract_df")
    ctx.con.unregister("_extract_df")


# --------------------------------------------------------------------------- files
def _prepare_cloud(con: duckdb.DuckDBPyConnection, path: str) -> None:
    if path.startswith(("az://", "azure://", "abfss://")):
        con.execute("INSTALL azure; LOAD azure;")
        from cdp.secrets import get_secret
        if get_secret("AZURE_STORAGE_CONNECTION_STRING"):
            con.execute("CREATE OR REPLACE SECRET cdp_azure (TYPE azure, CONNECTION_STRING "
                        f"{_quote(get_secret('AZURE_STORAGE_CONNECTION_STRING'))})")
        else:
            account = os.environ.get("AZURE_STORAGE_ACCOUNT")
            con.execute("CREATE OR REPLACE SECRET cdp_azure (TYPE azure, PROVIDER credential_chain"
                        + (f", ACCOUNT_NAME {_quote(account)}" if account else "") + ")")
    else:
        con.execute("INSTALL httpfs; LOAD httpfs;")
        if path.startswith(("s3://", "s3a://")):
            con.execute("CREATE OR REPLACE SECRET cdp_s3 (TYPE s3, PROVIDER credential_chain)")
        elif path.startswith(("gs://", "gcs://")) and os.environ.get("GCS_HMAC_KEY_ID"):
            con.execute(f"CREATE OR REPLACE SECRET cdp_gcs (TYPE gcs, KEY_ID {_quote(os.environ['GCS_HMAC_KEY_ID'])}, "
                        f"SECRET {_quote(_env('GCS_HMAC_SECRET'))})")


def extract_file(ctx: ExtractContext) -> None:
    conn = ctx.connector
    read = {**ctx.source_type.get("read", {}), **conn.get("read", {})}
    fmt = conn.get("format") or read.get("format", "csv")
    raw_path = _expand_env(conn["path"])
    if raw_path.startswith(CLOUD_SCHEMES):
        _prepare_cloud(ctx.con, raw_path)
        path = raw_path
    else:
        local = SOURCES_DIR / ctx.tenant_id / raw_path if not os.path.isabs(raw_path) else raw_path
        path = str(local).replace("\\", "/")
        if not any(ch in raw_path for ch in "*?[") and not os.path.exists(path):
            raise SourceError(f"File not found: {path}")

    if fmt == "excel":
        files = sorted(glob.glob(path)) if any(ch in path for ch in "*?[") else [path]
        if not files:
            raise SourceError(f"No files match {path}")
        frames = [pd.read_excel(f, sheet_name=read.get("sheet", 0), dtype=str, nrows=ctx.limit) for f in files]
        _store(ctx, pd.concat(frames, ignore_index=True))
        return
    if fmt == "csv":
        opts = ["all_varchar = true", "header = true", "union_by_name = true", "filename = false"]
        if read.get("delimiter"):
            opts.append(f"delim = {_quote(read['delimiter'])}")
        reader = f"read_csv({_quote(path)}, {', '.join(opts)})"
    elif fmt in ("jsonl", "json"):
        opts = [f"format = {_quote('newline_delimited' if fmt == 'jsonl' else 'auto')}"]
        if read.get("columns"):
            cols = ", ".join(f"{_quote(k)}: {_quote(v)}" for k, v in read["columns"].items())
            opts.append(f"columns = {{{cols}}}")
        reader = f"read_json({_quote(path)}, {', '.join(opts)})"
    elif fmt == "parquet":
        reader = f"read_parquet({_quote(path)}, union_by_name = true)"
    else:
        raise SourceError(f"Unsupported file format '{fmt}'")
    try:
        ctx.con.execute(f"CREATE OR REPLACE TEMP TABLE _extract AS SELECT * FROM {reader}{_limit_sql(ctx)}")
    except (duckdb.IOException, duckdb.InvalidInputException) as exc:
        raise SourceError(f"Could not read {path}: {str(exc).splitlines()[0]}") from exc


# --------------------------------------------------------------------------- databases
def _base_query(conn: dict) -> str:
    return conn["query"] if conn.get("query") else f"SELECT * FROM {conn['table']}"


def _incremental(query: str, conn: dict, watermark: str | None, placeholder: str) -> tuple[str, list]:
    column = conn.get("incremental", {}).get("column")
    if not column or watermark is None:
        return query, []
    return f"SELECT * FROM ({query}) AS src WHERE {column} > {placeholder}", [watermark]


def extract_database(ctx: ExtractContext) -> None:
    conn = ctx.connector
    engine = conn["engine"].lower()
    dsn = _env(conn["connection_env"])
    query = _base_query(conn)

    if engine in ("odbc", "sqlserver", "mssql", "oracle", "db2", "snowflake"):
        try:
            import pyodbc
        except ImportError as exc:
            raise SourceError("pyodbc is not installed (pip install pyodbc) and an ODBC driver is required") from exc
        sql, params = _incremental(query, conn, ctx.watermark, "?")
        try:
            with pyodbc.connect(dsn, timeout=conn.get("timeout", 30), readonly=True) as db:
                cursor = db.cursor()
                cursor.execute(sql, params)
                columns = [c[0] for c in cursor.description]
                frames = []
                while batch := cursor.fetchmany(ctx.limit or conn.get("batch_size", 50_000)):
                    frames.append(pd.DataFrame.from_records(batch, columns=columns))
                    if ctx.limit:
                        break
        except pyodbc.Error as exc:
            raise SourceError(f"ODBC error: {exc}") from exc
        _store(ctx, pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=columns))
    elif engine == "sqlite":
        sql, params = _incremental(query, conn, ctx.watermark, "?")
        if ctx.limit:
            sql = f"SELECT * FROM ({sql}){_limit_sql(ctx)}"
        try:
            with sqlite3.connect(f"file:{dsn}?mode=ro", uri=True) as db:
                _store(ctx, pd.read_sql_query(sql, db, params=params))
        except (sqlite3.Error, pd.errors.DatabaseError) as exc:
            raise SourceError(f"SQLite error: {exc}") from exc
    elif engine in ("postgres", "postgresql", "mysql"):
        ext = "postgres" if engine.startswith("postgres") else "mysql"
        ctx.con.execute(f"INSTALL {ext}; LOAD {ext};")
        ctx.con.execute(f"ATTACH {_quote(dsn)} AS _src (TYPE {ext}, READ_ONLY)")
        try:
            inner = f"SELECT * FROM {ext}_query('_src', {_quote(query)})"
            column = conn.get("incremental", {}).get("column")
            if column and ctx.watermark is not None:
                inner = f"SELECT * FROM ({inner}) WHERE {column} > {_quote(ctx.watermark)}"
            ctx.con.execute(f"CREATE OR REPLACE TEMP TABLE _extract AS {inner}{_limit_sql(ctx)}")
        except duckdb.Error as exc:
            raise SourceError(f"{ext} error: {exc}") from exc
        finally:
            ctx.con.execute("DETACH _src")
    else:
        raise SourceError(f"Unsupported database engine '{engine}'")


# --------------------------------------------------------------------------- REST
def _dig(obj, path: str | None):
    for part in (path.split(".") if path else []):
        obj = obj.get(part) if isinstance(obj, dict) else None
    return obj


def extract_rest(ctx: ExtractContext) -> None:
    conn = ctx.connector
    headers = {k: _expand_env(v) for k, v in conn.get("headers", {}).items()}
    params = dict(conn.get("params", {}))
    inc = conn.get("incremental", {})
    if inc.get("param") and ctx.watermark is not None:
        params[inc["param"]] = ctx.watermark
    paging = conn.get("pagination", {})
    url, page, records = _expand_env(conn["url"]), paging.get("start", 1), []
    for _ in range(1 if ctx.limit else conn.get("max_pages", 1000)):
        query = dict(params)
        if paging.get("type") == "page":
            query[paging.get("param", "page")] = page
            if paging.get("size_param"):
                query[paging["size_param"]] = paging.get("size", 100)
        full = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(query) if query else url
        request = urllib.request.Request(full, headers={"Accept": "application/json", **headers})
        try:
            with urllib.request.urlopen(request, timeout=conn.get("timeout", 60)) as resp:
                body = json.load(resp)
        except Exception as exc:  # urllib raises several unrelated types
            raise SourceError(f"REST call failed for {url}: {exc}") from exc
        batch = _dig(body, conn.get("records_path")) if conn.get("records_path") else body
        batch = batch or []
        records.extend(batch)
        if paging.get("type") == "next_url":
            url = _dig(body, paging.get("path", "next"))
            params = {}
            if not url:
                break
        elif paging.get("type") == "page":
            if len(batch) < paging.get("size", 100):
                break
            page += 1
        else:
            break
    df = pd.json_normalize(records, sep="_") if records else pd.DataFrame()
    _store(ctx, df)


EXTRACTORS = {"file": extract_file, "database": extract_database, "rest": extract_rest}


def extract(ctx: ExtractContext) -> int:
    """Run the source's connector; returns the number of rows extracted into `_extract`."""
    EXTRACTORS[ctx.connector["kind"]](ctx)
    return ctx.con.execute("SELECT count(*) FROM _extract").fetchall()[0][0]


def preview(tenant_id: str, source: dict, source_type: dict | None = None, limit: int = 50) -> pd.DataFrame:
    """Test a connection: fetch up to `limit` raw rows into an in-memory database. Raises SourceError."""
    con = duckdb.connect(":memory:")
    try:
        extract(ExtractContext(con, tenant_id, source, source_type or {}, limit=limit))
        return con.execute(f"SELECT * FROM _extract LIMIT {int(limit)}").df()
    except duckdb.Error as exc:
        raise SourceError(str(exc).splitlines()[0]) from exc
    finally:
        con.close()
