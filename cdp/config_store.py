"""Write access to the configuration, used by the admin UI.

Every change is validated against the *whole* configuration before anything is written; the previous file
is copied to config/.history/ and the change is appended to config/.history/audit.log. A rejected change
leaves the files untouched.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

from cdp import config
from cdp.config import CONFIG_DIR, ConfigError, validate
from cdp.mapping import output_sql

HISTORY_DIR = CONFIG_DIR / ".history"
ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


def _clear_caches() -> None:
    config.load_tenants.cache_clear()
    config.load_source_types.cache_clear()


def _current() -> tuple[list[dict], dict[str, dict]]:
    tenants = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((CONFIG_DIR / "tenants").glob("*.json"))]
    return tenants, config.load_source_types.__wrapped__()


def _audit(user: str, action: str, target: str) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_DIR / "audit.log", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "user": user, "action": action,
                             "target": target}) + "\n")


def _write(path: Path, data: dict | None, user: str, action: str) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(path, HISTORY_DIR / f"{stamp}__{path.parent.name}__{path.name}")
    if data is None:
        path.unlink(missing_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    _audit(user, action, f"{path.parent.name}/{path.name}")
    _clear_caches()


def _check(tenants: list[dict], source_types: dict[str, dict]) -> None:
    problems = validate(tenants, source_types)
    if problems:
        raise ConfigError(problems)


# --------------------------------------------------------------------------- tenants
def save_tenant(tenant: dict, user: str = "admin") -> None:
    if not ID_RE.match(tenant.get("tenant_id", "")):
        raise ConfigError(["Client id must start with a letter and use lowercase letters, digits or underscores"])
    tenants, types = _current()
    others = [t for t in tenants if t["tenant_id"] != tenant["tenant_id"]]
    _check(others + [tenant], types)
    _write(CONFIG_DIR / "tenants" / f"{tenant['tenant_id']}.json", tenant, user, "save client")


def delete_tenant(tenant_id: str, user: str = "admin") -> None:
    _write(CONFIG_DIR / "tenants" / f"{tenant_id}.json", None, user, "delete client")


def save_source(tenant_id: str, source: dict, original_name: str | None = None, user: str = "admin") -> None:
    """Add or replace one source of a client (original_name = the name being edited, for renames)."""
    tenants, _ = _current()
    tenant = next(t for t in tenants if t["tenant_id"] == tenant_id)
    replaced = original_name or source["name"]
    sources = [s for s in tenant["sources"] if s["name"] != replaced]
    position = next((i for i, s in enumerate(tenant["sources"]) if s["name"] == replaced), len(sources))
    sources.insert(position, source)
    save_tenant({**tenant, "sources": sources}, user)


def delete_source(tenant_id: str, source_name: str, user: str = "admin") -> None:
    tenants, _ = _current()
    tenant = next(t for t in tenants if t["tenant_id"] == tenant_id)
    save_tenant({**tenant, "sources": [s for s in tenant["sources"] if s["name"] != source_name]}, user)


# --------------------------------------------------------------------------- source types (mappings)
def save_source_type(name: str, source_type: dict, user: str = "admin") -> None:
    if not ID_RE.match(name):
        raise ConfigError(["Mapping name must start with a letter and use lowercase letters, digits or underscores"])
    tenants, types = _current()
    _check(tenants, {**types, name: source_type})
    _write(CONFIG_DIR / "source_types" / f"{name}.json", source_type, user, "save mapping")


def delete_source_type(name: str, user: str = "admin") -> None:
    tenants, _ = _current()
    users = [f"{t['tenant_id']}/{s['name']}" for t in tenants for s in t["sources"] if s["type"] == name]
    if users:
        raise ConfigError([f"Mapping '{name}' is used by: {', '.join(users)}"])
    _write(CONFIG_DIR / "source_types" / f"{name}.json", None, user, "delete mapping")


def mapping_preview(tenant: dict, source: dict, source_type: dict, raw: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Apply a mapping to sample raw rows and return the standard-model result per output entity.

    Raises duckdb.Error with the database's message when an expression is wrong.
    """
    con = duckdb.connect(":memory:")
    try:
        con.execute("CREATE SCHEMA bronze")
        sample = raw.astype("string")  # noqa: F841 - read by DuckDB
        con.execute(f'CREATE TABLE bronze."{tenant["tenant_id"]}__{source["name"]}" AS '
                    "SELECT *, now()::TIMESTAMP AS _loaded_at FROM sample")
        results = {}
        for out in source_type["outputs"]:
            results[out["entity"]] = con.execute(output_sql(tenant, source, source_type, out)).df().drop(
                columns=["tenant_id", "source_name", "namespace", "customer_namespace", "_loaded_at"],
                errors="ignore")
        return results
    finally:
        con.close()


def save_ai_settings(settings: dict, user: str = "admin") -> None:
    """Save the AI assistant provider settings (never the key: that lives in the credential store)."""
    from cdp.llm import PROVIDERS
    provider = settings.get("provider")
    problems = []
    if provider not in PROVIDERS:
        problems.append(f"Choose a provider: {', '.join(PROVIDERS)}")
    else:
        if not (settings.get("model") or PROVIDERS[provider]["default_model"]):
            problems.append("Enter the model" + (" (deployment) name." if provider == "azure_openai" else " name."))
        base = settings.get("base_url", "")
        if PROVIDERS[provider]["needs_base_url"] and not re.match(r"^https?://", base):
            problems.append("Enter the endpoint / base URL, starting with https:// (or http:// for a local server).")
    if problems:
        raise ConfigError(problems)
    clean = {k: settings.get(k) for k in ("provider", "model", "base_url", "api_version", "share_result_rows")}
    _write(CONFIG_DIR / "ai.json", clean, user, "save AI settings")


def audit_log(limit: int = 200) -> list[dict]:
    path = HISTORY_DIR / "audit.log"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    return [json.loads(line) for line in reversed(lines)]
